"""F3: image failure handling end to end, through orchestrator.submit
with a MOCKED Codex process (patch src.adapter_codex.run_process).

Covers Stage E scenarios 1-12: success, tool unavailable, nonzero exit,
timeout, incomplete JSON events, no artifact, corrupt PNG, out-of-bounds
path, copy failure, approval refused / approved / unclear, plus
state/receipt consistency and no automatic re-run of real generation.
No real image tool is ever invoked.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PIL import Image

from src import adapter_codex, image_approval
from src.orchestrator import submit, approve_image
from src.store import TaskStore

WORKSPACE = os.path.expanduser("~/workspace")
# Fresh checkouts may not have ~/workspace yet; the suite creates it
# rather than failing in setUp (candidate portability fix; production
# tests are unchanged).
os.makedirs(WORKSPACE, exist_ok=True)
PROMPT = ("Create one original PNG image of a small blue ceramic coffee "
          "cup on a plain white background, viewed from a slightly "
          "elevated angle. Minimal studio product photography, soft "
          "natural shadows, no text, no logo, no people.")


def mock_raw(rc=0, stdout="", stderr="", timed_out=False, spawn_error=None):
    return {"rc": rc, "stdout": stdout, "stderr": stderr,
            "elapsed_ms": 1234, "timed_out": timed_out,
            "spawn_error": spawn_error, "killed_group": False,
            "residual_pids": []}


def make_rgb_png(path, size=(16, 12)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (0, 0, 255)).save(path)
    return path


class ImageE2ETestBase(unittest.TestCase):
    def setUp(self):
        adapter_codex.configure_real_image(True)
        # The version check runs before any executor: without the pinned
        # Codex CLI installed it refuses with SPAWN_FAILED and the mocked
        # run_process is never reached. Stub only the version half of the
        # codex entry (same pattern as test_classifier_r7b1.TestMockE2E);
        # the real adapter_codex.run still executes under the mock.
        import src.orchestrator as _orch
        self._orch = _orch
        self._orig_codex = _orch.EXECUTORS["codex"]
        _orch.EXECUTORS["codex"] = (
            adapter_codex.run,
            lambda executor=None: ("0.164.0-alpha.9", True))
        self.base = tempfile.mkdtemp(prefix="r6c-e2e-", dir=WORKSPACE)
        # R7-E: these tests deliberately exercise the real-image path
        # (mocked process); they opt in via explicit config enablement.
        os.makedirs(os.path.join(self.base, "config"), exist_ok=True)
        with open(os.path.join(self.base, "config", "config.json"), "w") as f:
            json.dump({"external_calls_enabled": True}, f)
        self.gen = os.path.join(self.base, "generated_images")
        os.makedirs(self.gen)
        self._orig_gen = adapter_codex.GENERATED_IMAGES_DIR
        adapter_codex.GENERATED_IMAGES_DIR = self.gen
        make_rgb_png(os.path.join(self.gen, "old.png"))

    def tearDown(self):
        adapter_codex.configure_real_image(False)
        adapter_codex.GENERATED_IMAGES_DIR = self._orig_gen
        self._orch.EXECUTORS["codex"] = self._orig_codex
        shutil.rmtree(self.base, ignore_errors=True)

    def _submit(self, raw, create=None):
        """Submit the R6-B prompt with a mocked Codex process.
        `create` runs inside the mock (agent side effect). Returns
        (summary, mock)."""
        def _mock(*args, **kwargs):
            if create:
                create()
            return raw
        with mock.patch.object(adapter_codex, "run_process",
                               side_effect=_mock) as rp:
            summary = submit(PROMPT, input_text=PROMPT, base=self.base)
        return summary, rp

    def _db_row(self, task_id):
        conn = sqlite3.connect(os.path.join(self.base, "state", "tasks.db"))
        try:
            row = conn.execute(
                "SELECT status, result_json, receipt_reference FROM tasks "
                "WHERE task_id=?", (task_id,)).fetchone()
            return row
        finally:
            conn.close()

    def _receipt(self, task_id):
        path = os.path.join(self.base, "state", "receipts",
                            f"{task_id}.attempt1.json")
        with open(path) as f:
            return json.load(f)

    def _assert_state_receipt_consistent(self, task_id, expect_adopted):
        status, result_json, receipt_ref = self._db_row(task_id)
        receipt = self._receipt(task_id)
        self.assertEqual(status, "adopted" if expect_adopted else "fallback_to_muse")
        self.assertEqual(json.loads(result_json)["success"], expect_adopted)
        self.assertEqual(receipt["result"]["success"], expect_adopted)
        self.assertTrue(os.path.isfile(receipt_ref))
        self.assertEqual(receipt["task_id"], task_id)


class TestImageSuccessAndApproval(ImageE2ETestBase):
    def test_success_adopted_and_shown(self):
        summary, rp = self._submit(
            mock_raw(stdout='{"text": "Created your PNG image."}'),
            create=lambda: make_rgb_png(
                os.path.join(self.gen, "sess-1", "exec-a.png")))
        self.assertEqual(summary["status"], "adopted")
        self.assertEqual(summary["image_state"], "shown_to_user")
        self.assertFalse(summary["image_usable"])
        self.assertTrue(os.path.isfile(summary["artifact_path"]))
        rp.assert_called_once()  # exactly one (mocked) generation
        self._assert_state_receipt_consistent(summary["task_id"], True)

    def test_approval_approved(self):
        summary, _ = self._submit(
            mock_raw(stdout='{"text": "Created your PNG image."}'),
            create=lambda: make_rgb_png(
                os.path.join(self.gen, "sess-1", "exec-a.png")))
        task_id = summary["task_id"]
        out = approve_image(task_id, base=self.base)
        self.assertTrue(out["ok"])
        store = TaskStore(os.path.join(self.base, "state", "tasks.db"))
        try:
            rec = image_approval.get(store.conn, task_id)
        finally:
            store.close()
        self.assertEqual(rec["state"], "approved_for_use")
        self.assertTrue(image_approval.is_usable(rec["state"]))

    def test_approval_refused_stays_shown_and_unusable(self):
        # "Refusal" = no approval call: the draft stays shown, unusable.
        summary, _ = self._submit(
            mock_raw(stdout='{"text": "Created your PNG image."}'),
            create=lambda: make_rgb_png(
                os.path.join(self.gen, "sess-1", "exec-a.png")))
        store = TaskStore(os.path.join(self.base, "state", "tasks.db"))
        try:
            rec = image_approval.get(store.conn, summary["task_id"])
        finally:
            store.close()
        self.assertEqual(rec["state"], "shown_to_user")
        self.assertFalse(image_approval.is_usable(rec["state"]))

    def test_approval_unclear_keeps_safe_state(self):
        # No explicit approval decision: same safe state as refused.
        summary, _ = self._submit(
            mock_raw(stdout='{"text": "Created your PNG image."}'),
            create=lambda: make_rgb_png(
                os.path.join(self.gen, "sess-1", "exec-a.png")))
        store = TaskStore(os.path.join(self.base, "state", "tasks.db"))
        try:
            rec = image_approval.get(store.conn, summary["task_id"])
        finally:
            store.close()
        self.assertEqual(rec["state"], "shown_to_user")
        self.assertFalse(image_approval.is_usable(rec["state"]))

    def test_illegal_approval_transition_rejected(self):
        store = TaskStore(os.path.join(self.base, "state", "tasks.db"))
        try:
            image_approval.record_draft(store.conn, "t-x", "/tmp/x.png")
            with self.assertRaises(ValueError):
                # draft_generated -> approved_for_use skips shown_to_user.
                image_approval.transition(store.conn, "t-x", "approved_for_use")
        finally:
            store.close()


class TestImageFailures(ImageE2ETestBase):
    def _assert_fallback_once(self, summary, rp, error_category):
        self.assertEqual(summary["status"], "fallback_to_muse")
        self.assertEqual(summary["error_category"], error_category)
        rp.assert_called_once()  # no automatic re-run of real generation
        self._assert_state_receipt_consistent(summary["task_id"], False)

    def test_tool_unavailable(self):
        summary, rp = self._submit(mock_raw(
            stdout='{"text": "imagegen skill unavailable for this session"}'))
        self._assert_fallback_once(summary, rp, "INVALID_OUTPUT")

    def test_nonzero_exit(self):
        summary, rp = self._submit(
            mock_raw(rc=1, stderr="agent crashed"),
            create=lambda: make_rgb_png(
                os.path.join(self.gen, "sess-1", "exec-a.png")))
        self._assert_fallback_once(summary, rp, "NONZERO_EXIT")

    def test_timeout(self):
        summary, rp = self._submit(
            mock_raw(rc=143, timed_out=True),
            create=lambda: make_rgb_png(
                os.path.join(self.gen, "sess-1", "exec-a.png")))
        self._assert_fallback_once(summary, rp, "TIMEOUT")

    def test_incomplete_json_events(self):
        # Truncated event stream + no artifact: not a success.
        summary, rp = self._submit(mock_raw(
            stdout='{"type": "item.completed", "item": {"type": "agent_me'))
        self._assert_fallback_once(summary, rp, "INVALID_OUTPUT")

    def test_no_new_files(self):
        summary, rp = self._submit(mock_raw(
            stdout='{"text": "Created your PNG image of a blue cup."}'))
        self._assert_fallback_once(summary, rp, "INVALID_OUTPUT")

    def test_corrupt_png(self):
        def _create():
            p = os.path.join(self.gen, "sess-1", "bad.png")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"not a png at all")
        summary, rp = self._submit(mock_raw(stdout="done"), create=_create)
        self._assert_fallback_once(summary, rp, "INVALID_OUTPUT")

    def test_path_out_of_bounds(self):
        outside = os.path.join(self.base, "evil", "generated_images", "x.png")
        make_rgb_png(outside)
        summary, rp = self._submit(mock_raw(stdout=f"see {outside}"))
        self._assert_fallback_once(summary, rp, "INVALID_OUTPUT")
        # The outside file was never copied into the task work dir.
        self.assertFalse(os.path.exists(os.path.join(
            self.base, "work", "t-20261111-0001", "t-20261111-0001.png")))

    def test_copy_failure(self):
        with mock.patch.object(adapter_codex.shutil, "copyfile",
                               side_effect=OSError("disk full")):
            summary, rp = self._submit(
                mock_raw(stdout="done"),
                create=lambda: make_rgb_png(
                    os.path.join(self.gen, "sess-1", "exec-a.png")))
        self._assert_fallback_once(summary, rp, "INVALID_OUTPUT")
        self.assertFalse(os.path.exists(os.path.join(
            self.base, "work", "t-20261111-0001", "t-20261111-0001.png")))


if __name__ == "__main__":
    unittest.main()

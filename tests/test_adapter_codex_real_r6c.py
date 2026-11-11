"""F2: Codex adapter real-image wiring (R6-C).

All Codex process calls are MOCKED (patch src.adapter_codex.run_process).
No real image tool is ever invoked. Fixtures are local PNGs in temp dirs.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PIL import Image

from src import adapter_codex
from src.router import route

WORKSPACE = os.path.expanduser("~/workspace")
os.makedirs(WORKSPACE, exist_ok=True)  # fresh checkouts may lack it
PROMPT = "generate an image of a blue cup"


def make_contract(seq=1):
    return route("image_generate", "image test", PROMPT, kind="image",
                 task_seq=seq)


def make_rgb_png(path, size=(16, 12)):
    Image.new("RGB", size, (0, 0, 255)).save(path)
    return path


def make_rgba_png(path, size=(16, 12)):
    Image.new("RGBA", size, (0, 0, 255, 128)).save(path)
    return path


def mock_raw(rc=0, stdout="", stderr="", timed_out=False, spawn_error=None):
    return {"rc": rc, "stdout": stdout, "stderr": stderr,
            "elapsed_ms": 1234, "timed_out": timed_out,
            "spawn_error": spawn_error, "killed_group": False,
            "residual_pids": []}


class RealImageTestBase(unittest.TestCase):
    def setUp(self):
        adapter_codex.configure_real_image(False)
        self.tmp = tempfile.mkdtemp(prefix="r6c-adapter-", dir=WORKSPACE)
        self.cwd = os.path.join(self.tmp, "work")
        os.makedirs(self.cwd)
        self.gen = os.path.join(self.tmp, "generated_images")
        os.makedirs(self.gen)
        self._orig_gen = adapter_codex.GENERATED_IMAGES_DIR
        adapter_codex.GENERATED_IMAGES_DIR = self.gen
        # Pre-existing file: must never be mistaken for a new artifact.
        make_rgb_png(os.path.join(self.gen, "old.png"))

    def tearDown(self):
        adapter_codex.configure_real_image(False)
        adapter_codex.GENERATED_IMAGES_DIR = self._orig_gen
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_png_in_gen(self, name="sess-1/exec-a.png", rgb=True):
        p = os.path.join(self.gen, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        (make_rgb_png if rgb else make_rgba_png)(p)
        return p


class TestFlagDefaultOff(RealImageTestBase):
    def test_default_is_off(self):
        self.assertFalse(adapter_codex.is_real_image_enabled())

    def test_flag_off_runs_simulated_path(self):
        with mock.patch.object(adapter_codex, "run_process") as rp:
            raw = adapter_codex.run(make_contract(1), PROMPT, self.cwd)
        rp.assert_not_called()
        self.assertIn("SIMULATED", raw["stdout"])
        self.assertTrue(os.path.isfile(raw["artifact_path"]))
        self.assertTrue(raw["image_check"]["ok"])

    def test_contract_cannot_enable_real_image(self):
        contract = make_contract(2)
        contract["real_image_generation_enabled"] = True
        contract["codex"] = {"real_image_generation_enabled": True}
        with mock.patch.object(adapter_codex, "run_process") as rp:
            raw = adapter_codex.run(contract, PROMPT, self.cwd)
        rp.assert_not_called()
        self.assertIn("SIMULATED", raw["stdout"])

    def test_configure_rejects_non_bool(self):
        for bad in ("true", 1, None, ["yes"]):
            self.assertFalse(adapter_codex.configure_real_image(bad))
        self.assertFalse(adapter_codex.is_real_image_enabled())

    def test_config_loader(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "config", "config.json.example")) as f:
            cfg = json.load(f)
        # The shipped template has the flag explicitly false.
        self.assertFalse(adapter_codex.real_image_enabled_from_config(cfg))
        self.assertTrue(adapter_codex.real_image_enabled_from_config(
            {"executors": {"codex": {"real_image_generation_enabled": True}}}))
        self.assertTrue(adapter_codex.real_image_enabled_from_config(
            {"codex.real_image_generation_enabled": True}))
        self.assertFalse(adapter_codex.real_image_enabled_from_config({}))
        self.assertFalse(adapter_codex.real_image_enabled_from_config(
            {"executors": {"codex": {"real_image_generation_enabled": "true"}}}))
        self.assertFalse(adapter_codex.real_image_enabled_from_config(None))
        self.assertFalse(adapter_codex.real_image_enabled_from_config("yes"))


class TestRealPathMock(RealImageTestBase):
    def setUp(self):
        super().setUp()
        adapter_codex.configure_real_image(True)

    def _run(self, raw, contract=None, create=None):
        """Run the adapter with a mocked process. `create` (if given) is
        invoked inside the mock, simulating the agent writing files
        DURING the call — after the adapter's before-snapshot."""
        def _mock(*args, **kwargs):
            if create:
                create()
            return raw
        with mock.patch.object(adapter_codex, "run_process",
                               side_effect=_mock) as rp:
            out = adapter_codex.run(contract or make_contract(1), PROMPT,
                                    self.cwd)
        return out, rp

    def test_command_construction(self):
        out, rp = self._run(mock_raw())
        argv = rp.call_args[0][0]
        self.assertEqual(argv[0], adapter_codex.CODEX_BIN)
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-6.1-sol")
        self.assertEqual(argv[argv.index("-s") + 1], "workspace-write")
        self.assertIn("--ephemeral", argv)
        self.assertEqual(argv[-2], "--json")
        self.assertEqual(argv[-1], PROMPT)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)

    def test_success_via_tool_path_rgb(self):
        new = os.path.join(self.gen, "sess-1", "exec-a.png")
        out, _ = self._run(
            mock_raw(stdout=f'{{"text": "Created {new}"}}'),
            create=lambda: self.new_png_in_gen("sess-1/exec-a.png", rgb=True))
        self.assertTrue(out["image_check"]["ok"])
        self.assertEqual(out["image_check"]["mode"], "RGB")
        self.assertFalse(out["image_check"]["rgba_ok"])  # RGB has no alpha
        self.assertEqual(out["image_state"], "draft_generated")
        self.assertTrue(os.path.isfile(out["artifact_path"]))
        self.assertTrue(out["response_text"].startswith("REAL image generated"))
        with open(out["artifact_path"], "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        # Copy hash matches the source.
        self.assertEqual(adapter_codex._sha256(out["artifact_path"]),
                         adapter_codex._sha256(new))

    def test_success_via_snapshot_only_rgba(self):
        # R6-B D1: the event stream carries no path; discovery by snapshot.
        out, _ = self._run(
            mock_raw(stdout='{"text": "Created your PNG image of a blue cup."}'),
            create=lambda: self.new_png_in_gen("sess-1/exec-a.png", rgb=False))
        self.assertTrue(out["image_check"]["ok"])
        self.assertEqual(out["image_check"]["mode"], "RGBA")
        self.assertTrue(out["image_check"]["rgba_ok"])

    def test_no_artifact(self):
        out, _ = self._run(mock_raw(
            stdout='{"text": "Created your PNG image of a blue cup."}'))
        self.assertFalse(out["image_check"]["ok"])
        self.assertEqual(out["image_check"]["error"], "no_artifact")
        self.assertIsNone(out["artifact_path"])

    def test_tool_unavailable(self):
        out, _ = self._run(mock_raw(
            stdout='{"text": "imagegen skill unavailable for this session"}'))
        self.assertEqual(out["image_check"]["error"], "tool_unavailable")

    def test_ambiguous_candidates(self):
        def _create():
            self.new_png_in_gen("sess-1/a.png")
            self.new_png_in_gen("sess-2/b.png")
        out, _ = self._run(mock_raw(stdout="done"), create=_create)
        self.assertEqual(out["image_check"]["error"], "ambiguous_candidates")

    def test_tool_path_not_among_new_files(self):
        outside = os.path.join(self.tmp, "evil", "generated_images", "x.png")
        os.makedirs(os.path.dirname(outside), exist_ok=True)
        make_rgb_png(outside)
        out, _ = self._run(mock_raw(stdout=f"see {outside}"))
        self.assertEqual(out["image_check"]["error"], "tool_path_not_new")
        # The outside file must NOT be copied into the work dir.
        self.assertFalse(os.path.exists(
            os.path.join(self.cwd, "t-20261111-0001.png")))

    def test_symlink_candidate(self):
        def _create():
            target = os.path.join(self.tmp, "real.png")
            make_rgb_png(target)
            link = os.path.join(self.gen, "sess-1", "exec-link.png")
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(target, link)
        out, _ = self._run(mock_raw(stdout="done"), create=_create)
        self.assertEqual(out["image_check"]["error"], "symlink_candidate")

    def test_invalid_png(self):
        def _create():
            p = os.path.join(self.gen, "sess-1", "bad.png")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"not a png at all")
        out, _ = self._run(mock_raw(stdout="done"), create=_create)
        self.assertEqual(out["image_check"]["error"], "invalid_png")

    def test_nonzero_exit(self):
        out, _ = self._run(
            mock_raw(rc=1, stderr="agent crashed"),
            create=lambda: self.new_png_in_gen())
        self.assertEqual(out["image_check"]["error"], "nonzero_exit")

    def test_timeout(self):
        out, _ = self._run(
            mock_raw(rc=143, timed_out=True),
            create=lambda: self.new_png_in_gen())
        self.assertEqual(out["image_check"]["error"], "timeout")

    def test_copy_failure_cleans_up(self):
        with mock.patch.object(adapter_codex.shutil, "copyfile",
                               side_effect=OSError("disk full")):
            out, _ = self._run(
                mock_raw(stdout="done"),
                create=lambda: self.new_png_in_gen())
        self.assertEqual(out["image_check"]["error"], "copy_failed")
        self.assertFalse(os.path.exists(
            os.path.join(self.cwd, "t-20261111-0001.png")))


class TestDiscoveryHelpers(unittest.TestCase):
    def test_snapshot_diff(self):
        d = tempfile.mkdtemp(prefix="r6c-disc-")
        try:
            before = adapter_codex.snapshot_generated_dir(d)
            p = os.path.join(d, "a.png")
            with open(p, "w") as f:
                f.write("x")
            self.assertEqual(adapter_codex.discover_new_files(before, d), [p])
            self.assertEqual(adapter_codex.discover_new_files(
                adapter_codex.snapshot_generated_dir(d), d), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_validate_png_general(self):
        d = tempfile.mkdtemp(prefix="r6c-png-")
        try:
            rgb = make_rgb_png(os.path.join(d, "rgb.png"))
            rgba = make_rgba_png(os.path.join(d, "rgba.png"))
            self.assertTrue(adapter_codex.validate_png_general(rgb)["ok"])
            self.assertEqual(adapter_codex.validate_png_general(rgb)["mode"], "RGB")
            self.assertTrue(adapter_codex.validate_png_general(rgba)["ok"])
            bad = os.path.join(d, "bad.png")
            with open(bad, "w") as f:
                f.write("nope")
            self.assertFalse(adapter_codex.validate_png_general(bad)["ok"])
            self.assertFalse(adapter_codex.validate_png_general(
                os.path.join(d, "missing.png"))["ok"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

"""Process-group kill (with log, fixing R1's missing output log),
residual scan, and Codex image-fixture validation.

No real CLI model calls here. The Codex sandbox escape probe (H4) lives
in tests/h_probes.py because it invokes the real `codex sandbox`.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import adapter_base, adapter_codex


class TestProcessGroup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r2b-proc-")
        self.log = os.path.join(self.tmp, "proc_test.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_process_group_kill_leaves_no_residual(self):
        # parent shell spawns a grandchild sleep; killing the group must
        # kill both. Log is written to disk (R1 weak spot fixed).
        script = "sleep 60 & echo grandchild $!; sleep 60"
        proc = subprocess.Popen(["bash", "-c", script],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, start_new_session=True, cwd=self.tmp)
        time.sleep(1.0)
        pgid = os.getpgid(proc.pid)
        before = subprocess.run(["pgrep", "-g", str(pgid)],
                                capture_output=True, text=True).stdout.split()
        self.assertGreaterEqual(len(before), 2, "expected parent + grandchild")
        os.killpg(pgid, 15)  # SIGTERM to the whole group
        proc.wait(timeout=10)
        # R5-A: poll until the group drains instead of a fixed sleep.
        # A zombie child is already dead (awaiting reaping) and must not
        # count as residual; a live child must still be reported.
        deadline = time.time() + 5.0
        after = None
        while time.time() < deadline:
            raw = subprocess.run(["pgrep", "-g", str(pgid)],
                                 capture_output=True, text=True).stdout.split()
            after = [p for p in raw
                     if not adapter_base._is_zombie(p.strip())]
            if not after:
                break
            time.sleep(0.2)
        with open(self.log, "w") as f:
            f.write(f"pgid={pgid} parent_rc={proc.returncode} "
                    f"before={len(before)} after={len(after)}\n")
        self.assertEqual(proc.returncode, -15)
        self.assertEqual(len(after), 0)
        self.assertTrue(os.path.isfile(self.log))

    def test_adapter_timeout_kills_group(self):
        r = adapter_base.run_process(
            ["bash", "-c", "sleep 60 & wait"], timeout=2, cwd=self.tmp)
        self.assertTrue(r["timed_out"])
        self.assertTrue(r["killed_group"])
        self.assertEqual(len(r["residual_pids"]), 0)

    def test_adapter_spawn_fail(self):
        r = adapter_base.run_process(["/nonexistent/binary-xyz"], timeout=5, cwd=self.tmp)
        self.assertIsNotNone(r["spawn_error"])
        self.assertIsNone(r["rc"])


class TestCodexImageFixtures(unittest.TestCase):
    """Simulated product validation only: fixtures are built locally with
    PIL, never produced by a live Codex image call (H5 DEFERRED)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r2b-img-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_png(self, name, mode, size=(16, 16), transparent_ratio=0.5):
        from PIL import Image
        path = os.path.join(self.tmp, name)
        if mode == "RGBA":
            im = Image.new("RGBA", size, (255, 0, 0, 255))
            px = im.load()
            n = int(size[0] * size[1] * transparent_ratio)
            for i in range(n):
                px[i % size[0], i // size[0]] = (0, 0, 0, 0)
        else:
            im = Image.new(mode, size, (255, 0, 0))
        im.save(path)
        return path

    def test_rgba_transparent_passes(self):
        p = self._make_png("ok.png", "RGBA", transparent_ratio=0.5)
        r = adapter_codex.validate_png(p, min_transparent_ratio=0.4)
        self.assertTrue(r["ok"])
        self.assertEqual(r["mode"], "RGBA")
        self.assertAlmostEqual(r["transparent_ratio"], 0.5, places=1)
        self.assertIn("NOT_DONE_BY_CODE", r["content_acceptance"])

    def test_rgb_no_alpha_fails(self):
        p = self._make_png("rgb.png", "RGB")
        r = adapter_codex.validate_png(p)
        self.assertFalse(r["ok"])

    def test_missing_file_fails(self):
        r = adapter_codex.validate_png(os.path.join(self.tmp, "nope.png"))
        self.assertFalse(r["ok"])

    def test_non_png_fails(self):
        p = os.path.join(self.tmp, "fake.png")
        with open(p, "w") as f:
            f.write("not a png")
        r = adapter_codex.validate_png(p)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "not a PNG")


class TestGeneratedImagePathExtraction(unittest.TestCase):
    """R5-B preparation: discover a generated image path from
    `codex exec --json` output. Mock JSONL only — no real generation.
    The event schema is unverified, so the extractor is schema-agnostic
    (matches a generated_images PNG path anywhere in a line)."""

    def test_extracts_path_from_jsonl(self):
        events = (
            '{"type":"session","id":"abc"}\n'
            '{"type":"tool","name":"image_gen","result":'
            '"/home/example/.codex/generated_images/abc/exec-1.png"}\n'
            '{"type":"done"}\n')
        self.assertEqual(
            adapter_codex.extract_generated_image_path(events),
            "/home/example/.codex/generated_images/abc/exec-1.png")

    def test_no_image_event_returns_none(self):
        events = '{"type":"session","id":"abc"}\n{"type":"done"}\n'
        self.assertIsNone(adapter_codex.extract_generated_image_path(events))

    def test_malformed_lines_are_skipped(self):
        events = 'not json at all\n{"type":"tool","path":"/x/generated_images/a.png"}\n'
        self.assertEqual(
            adapter_codex.extract_generated_image_path(events),
            "/x/generated_images/a.png")

    def test_empty_input_returns_none(self):
        self.assertIsNone(adapter_codex.extract_generated_image_path(""))
        self.assertIsNone(adapter_codex.extract_generated_image_path(None))


if __name__ == "__main__":
    unittest.main()

"""Adapter base: process execution with timeout, process-group kill,
residual scan. No model logic here. Stdlib only.
"""
import os
import signal
import subprocess
import time


class SpawnFailed(Exception):
    pass


def _is_zombie(pid):
    """True if pid is a zombie (defunct) process.

    A zombie holds no resources and cannot be killed; it only waits for
    its parent to reap it. Counting it as a leftover process is a false
    positive. Read /proc/<pid>/stat (state is the field right after the
    comm in parentheses). Any read failure is treated as "not a zombie"
    (conservative: count the pid).
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read()
        rparen = data.rfind(b")")
        if rparen < 0:
            return False
        fields = data[rparen + 2:].split()
        return bool(fields) and fields[0] == b"Z"
    except Exception:
        return False


def residual_pids_in_group(pgid, pattern):
    """Return pids in process group pgid whose cmdline contains pattern.

    Zombie processes are excluded: they are already dead and only await
    reaping, so they are not residual work.
    """
    try:
        out = subprocess.run(["pgrep", "-g", str(pgid), pattern],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    return [p for p in out.stdout.split()
            if p.strip() and not _is_zombie(p.strip())]


def run_process(argv, timeout, cwd, stdin_data=None, env_extra=None):
    """Run argv in its own process group; kill the whole group on timeout.

    Returns dict: rc, stdout, stderr, elapsed_ms, timed_out, killed_group,
    spawn_error (None or str).
    """
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env=env)
    except OSError as e:
        return {"rc": None, "stdout": "", "stderr": f"SPAWN_FAIL: {e}",
                "elapsed_ms": 0, "timed_out": False, "killed_group": False,
                "spawn_error": str(e)}
    try:
        out, err = proc.communicate(input=stdin_data, timeout=timeout)
        timed_out = False
        killed = False
    except subprocess.TimeoutExpired:
        timed_out = True
        killed = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            out, err = proc.communicate(timeout=10)
        err = (err or "") + "\n[adapter] hard timeout: process group terminated"
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    residual = []
    try:
        pgid = os.getpgid(proc.pid)
        residual = residual_pids_in_group(pgid, os.path.basename(argv[0]))
    except (ProcessLookupError, PermissionError, ValueError):
        residual = []
    return {"rc": proc.returncode, "stdout": out or "", "stderr": err or "",
            "elapsed_ms": elapsed_ms, "timed_out": timed_out,
            "killed_group": killed, "spawn_error": None,
            "residual_pids": residual}


def check_binary_exists(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def binary_version(binary, timeout=15):
    """Return (version_stdout, rc) for a CLI binary.

    A missing or non-executable binary is reported explicitly as
    ("NOT_INSTALLED", None) instead of shelling out to a path that does
    not exist: callers can then tell "executor unavailable" apart from
    "version drift". Never raises for a missing binary.
    """
    if not check_binary_exists(binary):
        return ("NOT_INSTALLED", None)
    r = run_process([binary, "--version"], timeout=timeout, cwd="/tmp")
    return ((r["stdout"] or "").strip(), r["rc"])

"""agy adapter (Antigravity CLI 1.5.5) — R2-D hardened.

R2-C measured (R2C-SECURITY-AUDIT §2):
- `--settings` deny-all works headless: read/write/command/mcp/URL all denied.
- Deny beats Allow even for a more specific allow (R2-D probe S1): an
  allow rule for the work dir inside a denied parent is still denied.
  Therefore the ONLY enforceable shapes are:
    * deny_all (default): prompt-carried input only, the model reads no
      files at all. Any input that lives on disk must be read by MUSE and
      pasted into the prompt.
    * strict: toolPermission strict — every tool call asks, headless
      auto-denies. A blunt fallback, not the default.
  There is NO `scoped` mode: it raises ValueError (fail-closed).
- `--sandbox` does NOT restrict the read tool (R2-C A5). Never rely on it.
- Deny rules propagate to subagents and survive symlink traversal (R2-C A6/A7).

Hard rules (R2A + R2-D):
- --model BEFORE -p. NEVER --json-schema, NEVER Argon,
  NEVER --dangerously-skip-permissions, never `remote-control`, never --continue.
- The settings file is written per task into the task work dir (0600),
  NEVER into production settings.json (which does not exist and must stay so).
- Fail-closed: if the settings file cannot be written or fails re-read
  verification, run() refuses without invoking the CLI.
"""
import json
import os
import tempfile

from .adapter_base import run_process

AGY_BIN = os.path.expanduser("~/.local/bin/agy")
PINNED_VERSION = "1.5.5"
ALLOWED_MODELS = {
    "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
    "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium",
}
FORBIDDEN_TOKENS = {
    "--dangerously-skip-permissions", "--json-schema", "remote-control",
    "--continue", "-c", "--conversation", "gemini-4-argon",
}

# R2-C A1: deny-all rule set, enforced by the CLI permission engine.
DENY_ALL_RULES = [
    "read_file(*)", "write_file(*)", "command(*)",
    "mcp(*)", "read_url(*)", "execute_url(*)",
]
ALLOWED_MODES = ("deny_all", "strict")


def security_mode(contract):
    """Return the agy security mode from the contract's security profile."""
    profile = contract.get("security_profile") or {}
    return profile.get("agy_mode", "deny_all")


def build_settings(work_dir, mode="deny_all"):
    """Write the per-task session settings file. Returns its path.

    Raises ValueError on unknown mode or verification failure (fail-closed).
    The file holds only deny rules — no credentials, no paths to secrets.
    """
    if mode == "deny_all":
        settings = {"permissions": {"deny": list(DENY_ALL_RULES)}}
    elif mode == "strict":
        settings = {"toolPermission": "strict"}
    elif mode == "scoped":
        raise ValueError(
            "scoped mode is not enforceable: deny beats allow even for a "
            "more specific allow (R2-D probe S1); use deny_all with "
            "prompt-carried input")
    else:
        raise ValueError(f"unknown agy security mode: {mode}")
    path = os.path.join(work_dir, "agy-settings.json")
    with open(path, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    # Re-read verification: the file the CLI will see must contain the rules.
    with open(path) as f:
        check = json.load(f)
    if mode == "deny_all":
        if check.get("permissions", {}).get("deny") != DENY_ALL_RULES:
            raise ValueError("settings verification failed: deny rules missing")
    elif mode == "strict":
        if check.get("toolPermission") != "strict":
            raise ValueError("settings verification failed: strict mode missing")
    return path


def build_command(contract, prompt, work_dir=None):
    """Build argv from an allowlist template. Raises ValueError on violation.

    work_dir: task isolation dir; the settings file is written there.
    When None (unit tests only), a private temp dir is used.
    Returns (argv, settings_path).
    """
    model = contract.get("model", "gemini-3.8-flash-medium")
    if model not in ALLOWED_MODELS:
        raise ValueError(f"model not allowed: {model}")
    fmt = contract.get("output_format", "text")
    if fmt not in ("text", "json"):
        raise ValueError(f"output_format not allowed: {fmt}")
    mode = security_mode(contract)
    if mode not in ALLOWED_MODES:
        raise ValueError(f"agy security mode not allowed: {mode}")
    for tok in (model, fmt, prompt):
        if tok in FORBIDDEN_TOKENS:
            raise ValueError(f"forbidden token in command: {tok}")
    settings_dir = work_dir or tempfile.mkdtemp(prefix="r2d-agy-settings-")
    os.makedirs(settings_dir, exist_ok=True)
    settings_path = build_settings(settings_dir, mode)
    soft = contract["timeout"]["soft_seconds"]
    argv = [AGY_BIN, "--model", model, "-p", prompt,
            "--output-format", fmt, "--print-timeout", f"{soft}s",
            "--settings", settings_path]
    for tok in argv:
        if tok in FORBIDDEN_TOKENS:
            raise ValueError(f"forbidden token in command: {tok}")
    return argv, settings_path


def parse_output(stdout, stderr):
    """Return (response_text, envelope_or_None, denied_actions).

    Text mode: response is stdout verbatim.
    JSON mode: envelope parsed; response is envelope['response'].
    A SUCCESS envelope with empty response is NOT success here --
    the caller (validator) treats empty output as failure.
    """
    denied = []
    stripped = stdout.strip()
    if stripped.startswith("{"):
        try:
            env = json.loads(stripped)
            denied = env.get("denied_actions", []) or []
            return env.get("response", ""), env, denied
        except json.JSONDecodeError:
            return stdout, None, denied
    return stdout, None, denied


def version():
    from .adapter_base import binary_version as _bv
    return _bv(AGY_BIN)


def run(contract, prompt, cwd):
    try:
        argv, settings_path = build_command(contract, prompt, cwd)
        mode = security_mode(contract)
    except (ValueError, OSError) as e:
        # Fail-closed: never invoke the CLI without verified deny settings.
        return {"rc": None, "stdout": "", "stderr": f"SECURITY_FAIL_CLOSED: {e}",
                "elapsed_ms": 0, "timed_out": False, "killed_group": False,
                "spawn_error": str(e), "response_text": "", "envelope": None,
                "denied_actions": [], "argv": [], "security_fail_closed": True,
                "security_mode": security_mode(contract)}
    raw = run_process(argv, timeout=contract["timeout"]["hard_seconds"],
                      cwd=cwd, stdin_data=None)
    response, envelope, denied = parse_output(raw["stdout"], raw["stderr"])
    raw["response_text"] = response
    raw["envelope"] = envelope
    raw["denied_actions"] = denied
    raw["argv"] = argv
    raw["security_settings_path"] = settings_path
    raw["security_mode"] = mode
    return raw

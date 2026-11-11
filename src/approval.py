"""Approval Gate: P0-P3 enforcement + R2-D unified security screen.

- P0 auto, P1 auto_if_validated, P2 human_confirm, P3 forbidden.
- External send/publish is NEVER executable by an adapter: any capability
  in SEND_CAPABILITIES is rejected here (P3 for executors; MUSE-only).
- Tasks carrying real customer PII or production-write intent are
  SECURITY_BLOCKED: never delegated.

R2-D security screen (deterministic, NOT prompt-only judgement):
The screen runs on the contract fields AND the prompt text, using fixed
patterns. It never asks the model whether something is safe. A hit returns
SECURITY_BLOCKED with a rule id; the audit record stores the rule id only —
never the matched value, never a prompt excerpt (desensitized by design).

R3-0 hardening (path checks 1–8):
  1. PATH_TRAVERSAL   relative `..` segments (incl. encoded forms).
  2. PROD_PATH        absolute paths outside the allowed isolation roots.
  3. SYMLINK_ESCAPE   an allowed-root path that resolves outside it.
  4. NORMALIZED_PATH  encoded / backslash / double-slash / file:// variants
                      are decoded before scanning, so obfuscation fails.
  5. NONEXISTENT_PATH an absolute path that does not exist is still judged
                      by its normalized form (unknown = blocked unless under
                      an allowed root).
  6. ENV_HOME         `$HOME`, `${HOME}`, `%HOME%`, `~` path forms.
  7. INDIRECT_ACCESS  asking for a sensitive file without naming its path
                      ("read the private key", "open the SSH key", ...).
  8. CREDENTIAL_PATH  credential file names (tokens, keys, auth files).

TEXT SCANNING IS AUXILIARY DEFENCE ONLY. It does not implement OS-level
read isolation (Codex has none; agy/Grok rely on their CLI permission
engines). A clean scan is not proof of isolation.
"""
import os
import re
import urllib.parse

SEND_CAPABILITIES = {"send_message", "publish_x", "send_email", "post_public"}

PII_MARKERS = ("id_number", "phone_real", "address_real", "policy_number")

# --- R2-D screen patterns (kept) ---
MOBILE_RE = re.compile(r"1[3-9]\d{9}")
IDCARD_RE = re.compile(r"\d{17}[\dXx]")
ABS_PATH_RE = re.compile(r"/[A-Za-z0-9_.\-/]+")
CREDENTIAL_NAMES = (
    "auth.json", "oauth-token", "oauth_token", "id_rsa", "id_ed25519",
    ".pem", ".p12", "credentials.json", "client_secret", ".netrc",
    "antigravity-oauth-token",
)
# --- R3-0 hardening patterns ---
# `..` as a path segment: start, or after whitespace/quote/paren/slash,
# followed by / or end. Applied to the NORMALIZED text (decoding already done).
TRAVERSAL_RE = re.compile(r"(?:^|[\s\"'`(=/:])\.\.(?:/|$)", re.M)
# $HOME / ${HOME} / %HOME% / ~/ / ~user/ / trailing ~.
# `~` must start a token (start, whitespace, quote, paren, colon, slash)
# so ordinary text like "a~b/" does not false-positive.
ENV_HOME_RE = re.compile(
    r"\$HOME|\$\{HOME\}|%HOME%|(?:^|[\s\"'`(=:/])~[A-Za-z0-9_]*?/", re.M)
# Asking for a sensitive file without naming a path. Multi-word phrases
# only: single words like "token" or "secret" false-positive too easily.
# Chinese phrases included: 私钥 (private key), 密码文件, 口令, ssh 密钥.
SENSITIVE_NOUNS = (
    "private key", "ssh key", "api key", "secret key", "auth token",
    "access token", "password file", "passwd file", "credentials file",
    "credential file", "oauth token", ".ssh",
    "私钥", "密码文件", "口令", "ssh 密钥",
)
# Paths an executor may legitimately be told about: its own task work dir
# and the named fictional acceptance fixtures. Everything else absolute is
# treated as a production/credential path and blocked.
# Allowed roots are computed from the operator's home directory at
# import time, so the public source carries no machine-specific paths.
# These are the isolated acceptance-fixture workspaces used by the
# project's own tests; operators may extend the tuple for their own
# fixtures. Everything else absolute is treated as a production /
# credential path and blocked.
_HOME = os.path.expanduser("~")
ALLOWED_PATH_ROOTS = (
    os.path.join(_HOME, "workspace/muse-orchestration-r2d/work"),
    os.path.join(_HOME, "workspace/muse-orchestration-r3-0/work"),
    os.path.join(_HOME, "workspace/muse-orchestration-r2c"),
    "/tmp/r2d-accept",
    "/tmp/r2c-agy",
    "/tmp/r2c-grok",
    "/tmp/r2c-codex",
    "/tmp/r3-0-fixtures",
)


def normalize_for_scan(text):
    """Decode obfuscated path spellings before scanning.

    Double URL-decoding catches %252e%2e; backslashes become slashes;
    `file://` prefixes are stripped to plain paths; duplicate slashes
    collapse. The normalized copy is used ONLY for scanning.
    """
    if not text:
        return ""
    t = urllib.parse.unquote(urllib.parse.unquote(text))
    t = t.replace("\\", "/")
    t = re.sub(r"file:/+", "/", t, flags=re.I)
    t = re.sub(r"/{2,}", "/", t)
    return t


def _text_to_screen(contract, prompt):
    parts = [
        contract.get("objective", "") or "",
        ((contract.get("context") or {}).get("input_text", "")) or "",
        " ".join((contract.get("context") or {}).get("input_refs", []) or []),
        prompt or "",
    ]
    return "\n".join(parts)


def _path_allowed(path):
    """True only if the RESOLVED path is under an allowed root.

    realpath resolves symlinks for existing components and normalizes
    lexically otherwise, so nonexistent paths are judged by normalized
    form (item 5) and symlinks by their target (item 3).
    """
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root + os.sep)
               for root in ALLOWED_PATH_ROOTS)


def screen_text(text):
    """Return the rule id of the first hit, or None. Desensitized: the
    return value never contains the matched text."""
    if not text:
        return None
    norm = normalize_for_scan(text)
    low = norm.lower()
    for name in CREDENTIAL_NAMES:
        if name in low:
            return "CREDENTIAL_PATH"
    if TRAVERSAL_RE.search(norm):
        return "PATH_TRAVERSAL"
    if ENV_HOME_RE.search(text) or ENV_HOME_RE.search(norm):
        return "ENV_HOME"
    for phrase in SENSITIVE_NOUNS:
        if phrase in low:
            return "INDIRECT_ACCESS"
    if MOBILE_RE.search(text) or IDCARD_RE.search(text):
        return "PII_PATTERN"
    for m in ABS_PATH_RE.finditer(norm):
        if not _path_allowed(m.group(0)):
            return "PROD_PATH"
    return None


def security_screen(contract, prompt=""):
    """Return (rule_id, detail) or (None, None)."""
    constraints = contract.get("constraints", {})
    if constraints.get("has_real_customer_pii"):
        return ("PII_FLAG", "contract declares real customer PII")
    if constraints.get("writes_production"):
        return ("PROD_WRITE", "contract declares a production write")
    if constraints.get("requires_system_permission"):
        return ("SYSTEM_PERMISSION", "contract requires unapproved system permission")
    if constraints.get("external_operation") and not constraints.get("human_confirmed"):
        return ("EXTERNAL_OP", "declared external operation without human confirmation")
    executor = contract.get("executor", "")
    profile = contract.get("security_profile") or {}
    if executor == "agy":
        if profile.get("agy_mode") not in ("deny_all", "strict"):
            return ("MISSING_SECURITY_CONFIG", "agy task lacks a verified security mode")
    elif executor == "grok":
        if not contract.get("tools"):
            return ("MISSING_SECURITY_CONFIG", "grok task lacks a --tools allowlist")
    elif executor == "codex":
        if contract.get("sandbox") not in ("read-only", "workspace-write"):
            return ("MISSING_SECURITY_CONFIG", "codex task lacks an explicit sandbox mode")
    rule = screen_text(_text_to_screen(contract, prompt))
    if rule:
        return (rule, "screened text matches a blocked pattern")
    return (None, None)


def gate(contract, human_confirmed=False, prompt=""):
    """Returns (decision, reason).
    decision: allow | needs_human | forbidden | security_blocked
    """
    risk = contract.get("risk_level", "P3")
    cap = contract.get("capability", "")
    constraints = contract.get("constraints", {})
    if cap in SEND_CAPABILITIES:
        return ("forbidden", f"capability {cap} is external-send; executors may never send/publish")
    rule, detail = security_screen(contract, prompt)
    if rule:
        return ("security_blocked", f"SECURITY_BLOCKED:{rule}: {detail}")
    if risk == "P0":
        return ("allow", "P0 auto")
    if risk == "P1":
        return ("allow", "P1 auto_if_validated")
    if risk == "P2":
        if human_confirmed:
            return ("allow", "P2 human confirmed")
        return ("needs_human", "P2 requires human confirmation before execution")
    return ("forbidden", f"risk level {risk} forbidden for delegation")

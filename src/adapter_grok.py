"""Grok adapter (Grok Build 1.0.77) — R2-D hardened.

R2-C measured (R2C-SECURITY-AUDIT §3):
- `--tools` misspelled names silently leave other tools available (H2).
  `--disallowed-tools` has the SAME fail-open pattern (R2-C G4 stderr:
  "names no tool, so it removes nothing"). BOTH lists are validated here
  against known tables; an unknown name refuses execution, never passes through.
- MCP meta-tools (`search_tool` / `use_tool`) are always on and reachable
  even with `--tools web_search` only (R2-C G3). They are removed by
  default via --disallowed-tools (R2-C G4).
- `--deny "Read(*)"` blocks read execution (R2-C G5); deny wins over allow
  and always-approve per vendor docs (22-permissions-and-safety.md).
- Stock profiles inject enabled optional tools before the allowlist
  (14-headless-mode.md); the whitelist is NOT the exact final toolset
  (R2-C G4: web_search alone yields web_search + open_page + open_page_with_find).
  The actual toolset is verified with probe_available_tools() (acceptance
  test), and requested/disallowed/deny lists are recorded in every receipt.

Hard rules (R2A + R2-D):
- --tools is REQUIRED (headless defaults to full tool access).
- --disallowed-tools "Agent,search_tool,use_tool" ALWAYS, plus contract extras.
- NEVER --permission-mode bypassPermissions, NEVER --sandbox strict
  (unavailable on this VM: no bubblewrap).
- --output-format json: parse envelope, take text, SECOND parse of the
  inner JSON; strip `thought` immediately, never log or store it.
"""
import json
import os
import re

from .adapter_base import run_process

GROK_BIN = os.path.expanduser("~/.grok/bin/grok")
PINNED_VERSION = "1.0.77"

# Tool IDs named in the vendor headless-mode doc (14-headless-mode.md).
KNOWN_TOOLS = {
    "web_search", "web_fetch", "read_file", "grep", "list_dir",
    "run_terminal_cmd", "search_replace", "write", "task", "Shell",
    "Await", "StrReplace", "EditNotebook", "Delete", "ReadLints",
    "SwitchMode", "AskQuestion", "CreatePlan", "CallMcpTool",
    "GetMcpTools", "FetchMcpResource", "ListMcpResources",
    "GenerateImage", "GenerateVideo", "CreateGoal", "UpdateGoal", "XSearch",
}
# Always-on MCP meta-tools (07-mcp-servers.md): never allowlisted, only removed.
META_TOOLS = {"search_tool", "use_tool"}
# Names legal in --disallowed-tools: any known tool + meta-tools + Agent.
REMOVABLE_TOOLS = KNOWN_TOOLS | META_TOOLS | {"Agent"}
# Default removal: no subagents, no MCP meta-tools. Ever.
DEFAULT_DISALLOWED = ["Agent", "search_tool", "use_tool"]
# --deny rule shape: ToolPrefix or ToolPrefix(glob). Only the Read(*) mapping
# is R2-C measured (G5); other prefixes are syntax-validated only.
DENY_RULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\(.*\))?$")
FORBIDDEN_TOKENS = {
    "--permission-mode", "bypassPermissions", "--sandbox", "strict",
}


def validate_tool_names(names, table, flag):
    unknown = [t for t in names if t not in table]
    if unknown:
        raise ValueError(
            f"unknown tool name(s) in {flag}, refusing (fail-open risk): {unknown}")
    return list(names)


def default_deny_rules(tools):
    """Deny rules applied unless the task needs the tool.

    Only Read(*) is R2-C measured (G5). When read_file IS in the allowlist,
    no Read deny is applied — the task needs it, and the gate + hygiene
    rules (no PII, no production paths) are the control instead.

    Fetch(*) is always denied (R4-F measured, 2026-11-11): the Grok
    agent's skill system loads skills (e.g. ~/.agents/skills/agent-reach,
    whose description matches any internet-research / X task) by Fetching
    the SKILL.md file, and a Fetch of any target needs headless approval
    that cannot be given, so the run is cancelled with empty output
    (R4 test B, twice). Denying Fetch routes the agent to the pre-approved
    web_search / web_fetch (open_page) tools instead: URL fetching still
    works under the deny (example.com probe), and the trend task then
    completes with real public content. Fetch is unusable in headless
    mode anyway (any Fetch cancels the run), so the deny removes no
    working capability. It also closes a local-read bypass: Fetch could
    read file:// paths that Read(*) denies.
    """
    rules = ["Fetch(*)"]
    if "read_file" not in (tools or []):
        rules.append("Read(*)")
    return rules


def validate_deny_rules(rules):
    bad = [r for r in (rules or []) if not DENY_RULE_RE.match(r)]
    if bad:
        raise ValueError(f"malformed --deny rule(s): {bad}")
    return list(rules or [])


def build_command(contract, prompt):
    tools = contract.get("tools")
    if not tools:
        raise ValueError("--tools allowlist is required; refusing full-access run")
    validate_tool_names(tools, KNOWN_TOOLS, "--tools")
    extra_disallowed = validate_tool_names(
        contract.get("disallowed_tools_extra") or [], REMOVABLE_TOOLS,
        "--disallowed-tools")
    disallowed = list(dict.fromkeys(DEFAULT_DISALLOWED + extra_disallowed))
    deny_rules = validate_deny_rules(
        contract.get("deny_rules") if contract.get("deny_rules") is not None
        else default_deny_rules(tools))
    argv = [GROK_BIN, "--single", prompt,
            "--tools", ",".join(tools),
            "--disallowed-tools", ",".join(disallowed),
            "--no-subagents",
            "--max-turns", str(contract.get("max_turns", 3)),
            "--output-format", "json"]
    for rule in deny_rules:
        argv += ["--deny", rule]
    for tok in argv:
        if tok in FORBIDDEN_TOKENS:
            raise ValueError(f"forbidden token in command: {tok}")
    return argv


def parse_output(stdout, stderr):
    """Parse the JSON envelope. Returns dict with keys:
    text, inner (second-parse result or None), stop_reason, thought_stripped(bool),
    cost_usd, session_id, parse_error.
    `thought` is dropped on purpose and never returned in full.
    """
    stripped = stdout.strip()
    try:
        env = json.loads(stripped)
    except json.JSONDecodeError as e:
        return {"text": stdout, "inner": None, "stop_reason": None,
                "thought_stripped": False, "cost_usd": None,
                "session_id": None, "parse_error": f"envelope JSON error: {e}"}
    text = env.get("text", "")
    inner = None
    inner_error = None
    t = (text or "").strip()
    if t.startswith("{") or t.startswith("["):
        try:
            inner = json.loads(t)
        except json.JSONDecodeError as e:
            inner_error = str(e)
    return {"text": text, "inner": inner,
            "inner_error": inner_error,
            "stop_reason": env.get("stopReason"),
            "thought_stripped": "thought" in env,
            "cost_usd": env.get("total_cost_usd"),
            "session_id": env.get("sessionId"),
            "parse_error": None}


def reported_tools(text):
    """Extract backticked tool names the model reports as available.

    Heuristic, for the acceptance probe only: the envelope does not list
    the toolset, so the model is asked to report it and the reply is
    scanned. Returns a sorted list of candidate tool names.
    """
    found = set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", text or ""))
    return sorted(found)


def probe_available_tools(cwd, timeout=90):
    """Ask Grok which tools it has, with the hardened default flags.

    Returns (reported_tool_list, raw). Used by the acceptance test, not
    per task (it costs a model call). A probe that reports a tool outside
    the expected set is a finding, not a pass.
    """
    contract = {"tools": ["web_search"], "max_turns": 2,
                "timeout": {"hard_seconds": timeout}}
    argv = build_command(contract,
                         "List every tool available to you in this session as "
                         "backticked names, one per line. Do not use web search.")
    raw = run_process(argv, timeout=timeout, cwd=cwd, stdin_data=None)
    parsed = parse_output(raw.get("stdout", ""), raw.get("stderr", ""))
    return reported_tools(parsed.get("text", "")), raw


def version():
    from .adapter_base import binary_version as _bv
    return _bv(GROK_BIN)


def run(contract, prompt, cwd):
    argv = build_command(contract, prompt)
    raw = run_process(argv, timeout=contract["timeout"]["hard_seconds"],
                      cwd=cwd, stdin_data=None)
    parsed = parse_output(raw["stdout"], raw["stderr"])
    raw["parsed"] = parsed
    raw["argv"] = argv
    # Audit trail: what was requested, removed and denied — for the receipt.
    raw["requested_tools"] = list(contract.get("tools") or [])
    raw["disallowed_tools"] = list(dict.fromkeys(
        DEFAULT_DISALLOWED + list(contract.get("disallowed_tools_extra") or [])))
    raw["deny_rules"] = validate_deny_rules(
        contract.get("deny_rules") if contract.get("deny_rules") is not None
        else default_deny_rules(contract.get("tools")))
    return raw

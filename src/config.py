"""Runtime config loading for the orchestration entry (R7-E).

The formal entry (`muse_orchestrate.py run`) previously never read
`config/config.json`, so external agents were dispatched whenever the
router matched — even with no config file at all. External calls are
now default-off: they run only when the operator explicitly enables
them in `config/config.json`.

Rules (fail-closed):
- A missing file, an unreadable file, invalid JSON, or a non-object
  top level all mean "disabled". The reason is recorded in the
  refusal; loading never raises out of the entry.
- Only a literal JSON `true` for `external_calls_enabled` enables.
  Strings ("true"), numbers (1), and nested variants do not.
- Local handling (muse_direct) and the Codex simulated-image path
  never need this flag: they make no external call.
- Enabling this flag does NOT bypass the security gate or the pinned
  version check: both still run in `execute_task` before any call.
- The Codex real-image flag stays separate and default-off
  (`adapter_codex.configure_real_image` /
  `real_image_enabled_from_config`). Both flags must be on for a real
  Codex image call; this flag alone never enables image generation.
"""
import json
import os

CONFIG_KEY = "external_calls_enabled"


def config_path(base):
    """Absolute path of the operator config file for this install."""
    return os.path.join(base, "config", "config.json")


def load_config(base):
    """Read the config file. Returns (config_dict, error_or_None).

    Never raises: every failure mode yields an empty config (disabled)
    plus a short reason string.
    """
    path = config_path(base)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, "config file missing"
    except (OSError, ValueError) as e:
        return {}, f"config unreadable: {e}"
    if not isinstance(data, dict):
        return {}, "config top level is not an object"
    return data, None


def external_calls_enabled(cfg):
    """Only a literal True enables external calls."""
    if not isinstance(cfg, dict):
        return False
    return cfg.get(CONFIG_KEY) is True


def requires_external_call(contract, real_image_enabled=False):
    """True when executing this contract would invoke an external CLI.

    agy and Grok always call their CLI. Codex calls its CLI for text
    and for real image generation; the simulated-image path (real
    image flag off) generates a local PNG and makes no external call.
    Unknown executors are treated as external (fail-closed).
    """
    contract = contract or {}
    executor = contract.get("executor")
    if executor in ("agy", "grok"):
        return True
    if executor == "codex":
        kind = (contract.get("expected_output") or {}).get("kind")
        if kind == "image" and not real_image_enabled:
            return False
        return True
    return True

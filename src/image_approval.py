"""Simulated image artifacts + user-approval state machine (R3).

Real image generation stays DISABLED (H5 DEFERRED, no user authorization).
This module produces a *simulated* artifact — a valid PNG generated locally
with the standard library only — so the routing, path guard, PNG validation
and approval flow can be exercised end to end without calling image_gen.

Approval states (R2A image lifecycle):
    draft_generated -> shown_to_user -> approved_for_use
Only approved_for_use may be used. Transitions are enforced here; the
store is the same SQLite DB (new table, created idempotently).
"""
import os
import struct
import time
import zlib

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_approvals (
    task_id TEXT PRIMARY KEY,
    artifact_path TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

STATES = ("draft_generated", "shown_to_user", "approved_for_use")
TRANSITIONS = {
    "draft_generated": ("shown_to_user",),
    "shown_to_user": ("approved_for_use",),
    "approved_for_use": (),
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())


def _png_chunk(ctype, data):
    chunk = ctype + data
    return struct.pack(">I", len(data)) + chunk + struct.pack(
        ">I", zlib.crc32(chunk) & 0xFFFFFFFF)


def generate_simulated_png(path, width=64, height=64):
    """Write a deterministic valid RGBA PNG (gradient + transparent corner).

    Pure stdlib; no model, no network, no PIL dependency.
    """
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter: none
        for x in range(width):
            # Transparent in the top-left 16x16 square, gradient elsewhere.
            alpha = 0 if (x < 16 and y < 16) else 255
            raw += bytes([x * 255 // max(width - 1, 1),
                          y * 255 // max(height - 1, 1), 128, alpha])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + _png_chunk(b"IHDR", ihdr)
           + _png_chunk(b"IDAT", zlib.compress(raw))
           + _png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return path


def validate_png_header(path):
    """Stdlib-only PNG check: magic + IHDR dimensions. No PIL needed."""
    if not os.path.isfile(path):
        return {"ok": False, "error": "file missing"}
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return {"ok": False, "error": "not a PNG"}
    if len(head) < 33 or head[12:16] != b"IHDR":
        return {"ok": False, "error": "no IHDR"}
    width, height = struct.unpack(">II", head[16:24])
    return {"ok": True, "size": (width, height), "error": None,
            "content_acceptance": "NOT_DONE_BY_CODE_requires_human_visual_check"}


def ensure_table(conn):
    conn.execute(SCHEMA)


def record_draft(conn, task_id, artifact_path):
    ensure_table(conn)
    now = _now()
    conn.execute(
        "INSERT OR REPLACE INTO image_approvals "
        "(task_id, artifact_path, state, created_at, updated_at) "
        "VALUES (?, ?, 'draft_generated', ?, ?)",
        (task_id, artifact_path, now, now))


def transition(conn, task_id, to_state):
    """Enforce the state machine. Raises ValueError on illegal moves."""
    ensure_table(conn)
    row = conn.execute(
        "SELECT state FROM image_approvals WHERE task_id = ?",
        (task_id,)).fetchone()
    if not row:
        raise ValueError(f"no image draft for task {task_id}")
    current = row[0]
    if to_state not in TRANSITIONS.get(current, ()):
        raise ValueError(f"illegal image transition {current} -> {to_state}")
    conn.execute(
        "UPDATE image_approvals SET state = ?, updated_at = ? WHERE task_id = ?",
        (to_state, _now(), task_id))


def get(conn, task_id):
    ensure_table(conn)
    row = conn.execute(
        "SELECT task_id, artifact_path, state FROM image_approvals "
        "WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        return None
    return {"task_id": row[0], "artifact_path": row[1], "state": row[2]}


def is_usable(state):
    """Only an explicitly user-approved artifact may be used."""
    return state == "approved_for_use"

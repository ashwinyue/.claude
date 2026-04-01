#!/usr/bin/env python3
"""
Review Skills — Gated background review using `claude -p --continue`.

This script does NOT call any external LLM API. Instead:
1. Reads ~/.claude/history.jsonl
2. Applies lightweight heuristics
3. If the session looks skill-worthy, spawns `claude -p --continue` with the
   self-improving skill loaded, letting Claude itself decide whether to create
   or update a skill.

Hook: Stop
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
PENDING_REVIEWS = Path.home() / ".claude" / ".pending-reviews.md"
STATE_PATH = Path.home() / ".claude" / ".skill_review_state.json"
SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "self-improving" / "SKILL.md"

# Heuristic patterns ------------------------------------------------------------------
_FAILURE_KEYWORDS = [
    r"error", r"failed", r"failure", r"panic", r"abort", r"exit \d+",
    r"not found", r"no such file", r"permission denied",
    r"cannot find", r"does not exist", r"undefined:",
    r"build failed", r"test failed", r"compilation error",
    r"command not found", r"syntax error", r"segfault",
]

_CORRECTION_KEYWORDS = [
    r"no,? that'?s (wrong|not right)",
    r"actually[,.]",
    r"you('re| are) wrong",
    r"stop doing",
    r"i (prefer|like|want|need)",
    r"always do",
    r"never do",
    r"remember that i",
    r"i told you before",
    r"(?:don'?t|do not) (?:split|break|extract|abstract)",
]

_PATTERN_KEYWORDS = [
    r"how do i",
    r"what'?s the best way to",
    r"gotcha",
    r"workaround",
    r"best practice",
    r"pattern for",
]

_ALL_PATTERNS = [
    ("failure", _FAILURE_KEYWORDS),
    ("correction", _CORRECTION_KEYWORDS),
    ("pattern", _PATTERN_KEYWORDS),
]

MIN_USER_MESSAGES = 3
MIN_TOTAL_CHARS = 200


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_reviewed_session": None}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _read_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception as e:
        logger.warning("Could not read history: %s", e)
        return []


def _get_recent_session(history: list[dict]) -> tuple[str | None, list[str]]:
    if not history:
        return None, []
    last_session = history[-1].get("sessionId")
    messages = []
    for entry in reversed(history):
        if entry.get("sessionId") != last_session:
            break
        display = entry.get("display", "").strip()
        if display:
            messages.append(display)
    return last_session, list(reversed(messages))


def _should_review(messages: list[str]) -> bool:
    if len(messages) < MIN_USER_MESSAGES:
        return False
    total_chars = sum(len(m) for m in messages)
    if total_chars < MIN_TOTAL_CHARS:
        return False
    return True


def _detect_signals(messages: list[str]) -> list[str]:
    signals = []
    combined = "\n".join(messages).lower()
    for label, patterns in _ALL_PATTERNS:
        for pat in patterns:
            if re.search(pat, combined, re.IGNORECASE):
                signals.append(label)
                break
    command_like = sum(1 for m in messages if re.search(r"`[^`]+`|run |execute |build |test |grep |find ", m))
    if command_like >= 2:
        signals.append("multi_command")
    return signals


def _append_pending_review(session_id: str, messages: list[str], signals: list[str]) -> None:
    PENDING_REVIEWS.parent.mkdir(parents=True, exist_ok=True)
    snippet = "\n".join(messages)
    if len(snippet) > 2000:
        snippet = snippet[:2000] + "\n... [truncated]"
    block = (
        f"\n## Review {datetime.now().isoformat()}\n"
        f"- session: {session_id}\n"
        f"- signals: {', '.join(signals)}\n"
        f"- messages:\n\n```\n{snippet}\n```\n"
    )
    with open(PENDING_REVIEWS, "a", encoding="utf-8") as f:
        f.write(block)


def _spawn_claude_review() -> int:
    prompt = (
        "You are running a post-session background review. "
        "The goal is to identify whether the just-finished conversation "
        "contained reusable knowledge, workflow patterns, or corrections "
        "worth saving as a skill.\n\n"
        "Check the current project's .claude/skills/ and ~/.claude/skills/ "
        "for existing skills before creating a new one. "
        "If nothing stands out, reply with exactly 'Nothing to save.' and stop.\n\n"
        "If you decide to act, use Read/Write/Edit tools to create or update a skill, "
        "or update ~/.claude/CLAUDE.md for broadly applicable insights.\n\n"
        "After any write, give a one-line summary: 'Created skill: <name>', "
        "'Updated skill: <name>', or 'Updated CLAUDE.md'."
    )

    cmd = [
        "claude",
        "-p",
        "--continue",
        "--allowedTools", "Read,Write,Edit,Bash",
        prompt,
    ]
    if SKILL_PATH.exists():
        cmd.extend(["--append-system-prompt-file", str(SKILL_PATH)])

    print(f"Spawning background review with: {' '.join(cmd[:6])} ...")
    # Detach so gateway shutdown doesn't kill the child.
    env = os.environ.copy()
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    result = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print(result.stdout)
    if result.stderr:
        print("stderr:", result.stderr, file=sys.stderr)
    return result.returncode


def main() -> int:
    history = _read_history()
    session_id, messages = _get_recent_session(history)

    if not _should_review(messages):
        print("Heuristic: session too short, skipping review.")
        return 0

    state = _load_state()
    if state.get("last_reviewed_session") == session_id:
        print("Already reviewed this session.")
        return 0

    signals = _detect_signals(messages)
    if signals:
        _append_pending_review(session_id, messages, signals)

    # Gate: only call claude -p if there are signals or messages look substantial
    if not signals and len(messages) < 6:
        print("No strong signals, skipping claude -p review.")
        state["last_reviewed_session"] = session_id
        _save_state(state)
        return 0

    rc = _spawn_claude_review()
    state["last_reviewed_session"] = session_id
    _save_state(state)
    return rc


if __name__ == "__main__":
    sys.exit(main())

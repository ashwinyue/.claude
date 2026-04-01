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

# Persistent debug log so we can inspect hook execution after session ends
_LOG_PATH = Path.home() / ".claude" / ".review_skills.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_LOG_PATH, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)

HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"
PENDING_REVIEWS = Path.home() / ".claude" / ".pending-reviews.md"
STATE_PATH = Path.home() / ".claude" / ".skill_review_state.json"
BG_LOG_PATH = Path.home() / ".claude" / ".review_skills_bg.log"
PENDING_DIR = Path.home() / ".claude" / ".pending-skills" / "pending"
APPROVED_DIR = Path.home() / ".claude" / ".pending-skills" / "approved"
REJECTED_DIR = Path.home() / ".claude" / ".pending-skills" / "rejected"
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
    r"保存成.*skill",
    r"整理成",
    r"完整流程",
    r"梳理",
    r"最佳实践",
    r"如何.*添加",
    r"如何.*注册",
    r"可复用",
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
    return {"last_reviewed_session": None, "last_message_count": 0, "last_total_chars": 0}


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
    """Find the most substantial recent session from history.

    history.jsonl is shared across all concurrent Claude Code terminals,
    so records from different sessions can be interleaved at the tail.
    We scan the last N entries, group by sessionId, and pick the session
    with the most total characters as the "recent" one.
    """
    if not history:
        return None, []

    # Scan last 200 entries — enough for a long session, not too slow.
    tail = history[-200:]
    sessions: dict[str, list[str]] = {}
    for entry in tail:
        sid = entry.get("sessionId")
        display = entry.get("display", "").strip()
        if sid and display:
            sessions.setdefault(sid, []).append(display)

    if not sessions:
        return None, []

    # Pick the session with the most total characters.
    best_session = max(
        sessions.items(),
        key=lambda item: sum(len(m) for m in item[1]),
    )[0]
    return best_session, sessions[best_session]


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


def _spawn_claude_review(session_id: str, messages: list[str], signals: list[str]) -> int:
    summary = "\n".join(messages[-30:])  # last 30 messages
    if len(summary) > 4000:
        summary = summary[:4000] + "\n... [truncated]"

    prompt = (
        "You are running a post-session background review.\n\n"
        "SESSION SUMMARY:\n"
        f"- sessionId: {session_id}\n"
        f"- signals: {', '.join(signals) or 'none'}\n"
        f"- last messages:\n\n{summary}\n\n"
        "TASK:\n"
        "1. Read the current project's .claude/skills/ and ~/.claude/skills/.\n"
        "2. Decide if the conversation contains reusable knowledge, a workflow, or a correction worth persisting.\n"
        "3. If nothing stands out, reply with exactly 'Nothing to save.' and stop.\n"
        "4. If you decide to save a skill, output ONLY a JSON object (no markdown fences, no extra text) with these keys:\n"
        '   {"action": "create", "name": "kebab-case-name", "local": true, "reason": "brief reason", "content": "full SKILL.md content with YAML frontmatter"}\n'
        "   - action: 'create' or 'edit'\n"
        "   - local: true for .claude/skills/ (project-local), false for ~/.claude/skills/\n"
        "5. After emitting the JSON, stop immediately.\n"
        "Do NOT use Read/Write/Edit tools yourself — just print the raw JSON string."
    )

    cmd = [
        "claude",
        "-p",
        prompt,
        "--continue",
        "--allowedTools", "Read,Bash",
        "--dangerously-skip-permissions",
    ]
    if SKILL_PATH.exists():
        cmd.extend(["--append-system-prompt-file", str(SKILL_PATH)])

    env = os.environ.copy()
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"

    BG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_fh = open(BG_LOG_PATH, "a", encoding="utf-8")
        log_fh.write(f"\n--- {datetime.now().isoformat()} Spawning review for {session_id} ---\n")
        log_fh.flush()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
            text=True,
        )
        logger.info("Detached claude -p for session %s", session_id)

        # Wait for it in a thread so the main Stop hook can return immediately
        def _capture():
            try:
                stdout, _ = process.communicate(timeout=120)
                log_fh.write(stdout)
                log_fh.flush()
                _parse_and_save_proposal(session_id, stdout)
            except subprocess.TimeoutExpired:
                process.kill()
                log_fh.write("\n[TIMEOUT after 120s]\n")
                log_fh.flush()
            finally:
                log_fh.close()

        import threading
        threading.Thread(target=_capture, daemon=True).start()
    except FileNotFoundError:
        logger.error("'claude' command not found in PATH")
        return 127
    except Exception as e:
        logger.exception("Failed to spawn detached claude -p: %s", e)
        return 1

    return 0


def _parse_and_save_proposal(session_id: str, stdout: str) -> None:
    """Parse the last JSON block from claude -p output and write it as a pending proposal."""
    # Try to find the last JSON object in the output
    text = stdout.strip()
    if not text:
        return
    if "Nothing to save." in text:
        logger.info("Background review decided: Nothing to save.")
        return

    # Look for the last {...} block
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.warning("No JSON proposal found in background review output.")
        return

    json_str = text[start:end + 1]
    try:
        proposal = json.loads(json_str)
    except Exception as e:
        logger.warning("Failed to parse JSON proposal: %s", e)
        return

    if not isinstance(proposal, dict) or proposal.get("action") not in ("create", "edit"):
        logger.warning("Invalid proposal structure.")
        return

    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    name = re.sub(r"[^a-z0-9_-]", "", str(proposal.get("name", "unknown")))
    filename = f"{ts}-{session_id[:8]}-{name}.json"
    path = PENDING_DIR / filename
    try:
        path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved pending proposal: %s", path)
    except Exception as e:
        logger.error("Failed to write pending proposal: %s", e)


def main() -> int:
    logger.info("review_skills started")
    history = _read_history()
    logger.debug("Read %d history entries", len(history))
    session_id, messages = _get_recent_session(history)
    logger.info("Session=%s messages=%d chars=%d", session_id, len(messages), sum(len(m) for m in messages))

    if not _should_review(messages):
        logger.info("Heuristic: session too short, skipping review.")
        return 0

    total_chars = sum(len(m) for m in messages)
    state = _load_state()
    already_reviewed = (
        state.get("last_reviewed_session") == session_id
        and state.get("last_message_count", 0) == len(messages)
        and state.get("last_total_chars", 0) == total_chars
    )
    if already_reviewed:
        logger.info("Already reviewed this session with identical content.")
        return 0

    signals = _detect_signals(messages)
    logger.info("Detected signals: %s", signals)
    if signals:
        _append_pending_review(session_id, messages, signals)

    # Gate: only call claude -p if there are signals or messages look substantial
    if not signals and len(messages) < 6:
        logger.info("No strong signals and <6 messages, skipping claude -p review.")
        state["last_reviewed_session"] = session_id
        state["last_message_count"] = len(messages)
        state["last_total_chars"] = total_chars
        _save_state(state)
        return 0

    # Save state *before* spawning so a gateway timeout / kill doesn't retry the
    # same session on the next Stop hook.
    state["last_reviewed_session"] = session_id
    state["last_message_count"] = len(messages)
    state["last_total_chars"] = total_chars
    _save_state(state)

    rc = _spawn_claude_review(session_id, messages, signals)
    logger.info("claude -p returned %d", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
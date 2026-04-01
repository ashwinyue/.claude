#!/usr/bin/env python3
"""
Approve Pending Skills — Batch-execute pending skill proposals.

Usage:
    python3 .claude/scripts/approve_pending_skills.py
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

PENDING_DIR = Path.home() / ".claude" / ".pending-skills" / "pending"
APPROVED_DIR = Path.home() / ".claude" / ".pending-skills" / "approved"
REJECTED_DIR = Path.home() / ".claude" / ".pending-skills" / "rejected"
SKILL_MANAGER = Path(__file__).resolve().parent / "skill_manager.py"


def main() -> int:
    files = sorted(PENDING_DIR.glob("*.json")) if PENDING_DIR.exists() else []
    if not files:
        print("No pending skill proposals.")
        return 0

    print(f"Approving {len(files)} pending proposal(s)...\n")
    ok = 0
    failed = 0

    for f in files:
        print(f"  {f.name} ...", end=" ")
        result = subprocess.run(
            ["python3", str(SKILL_MANAGER), "proposal", "--path", str(f)],
            capture_output=True,
            text=True,
        )
        try:
            out = json.loads(result.stdout.strip())
        except Exception:
            out = {"success": result.returncode == 0, "message": result.stdout.strip()}

        if out.get("success"):
            print("OK")
            APPROVED_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(APPROVED_DIR / f.name))
            ok += 1
        else:
            print("FAILED")
            err = out.get("error") or result.stderr.strip() or "unknown error"
            print(f"      -> {err}")
            REJECTED_DIR.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(REJECTED_DIR / f.name))
            failed += 1

    print(f"\nDone: {ok} approved, {failed} failed/moved to rejected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
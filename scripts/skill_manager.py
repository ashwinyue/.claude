#!/usr/bin/env python3
"""
Skill Manager — CLI tool for creating and editing Claude Code skills.

Heavily modeled after Hermes Agent's tools/skill_manager_tool.py.
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path.home() / ".claude" / "skills"
LOCAL_SKILLS_DIR = Path(".claude") / "skills"

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}


def _validate_name(name: str) -> str | None:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores. Must start with a letter or digit."
        )
    return None


def _validate_frontmatter(content: str) -> str | None:
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---)."
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return "SKILL.md frontmatter is not closed. Ensure you have a closing '---' line."
    yaml_content = content[3:end_match.start() + 3]
    parsed = {}
    for line in yaml_content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            parsed[key] = value
    if "name" not in parsed:
        return "Frontmatter must include 'name' field."
    if "description" not in parsed:
        return "Frontmatter must include 'description' field."
    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    body = content[end_match.end():].strip()
    if not body:
        return "SKILL.md must have content after the frontmatter."
    return None


def _validate_content_size(content: str, label: str = "SKILL.md") -> str | None:
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return (
            f"{label} content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,})."
        )
    return None


def _resolve_skill_dir(name: str, category: str | None = None, local: bool = False) -> Path:
    base = LOCAL_SKILLS_DIR if local else SKILLS_DIR
    if category:
        return base / category / name
    return base / name


def _find_skill(name: str, local: bool = False) -> Path | None:
    base = LOCAL_SKILLS_DIR if local else SKILLS_DIR
    if not base.exists():
        return None
    for skill_md in base.rglob("SKILL.md"):
        if skill_md.parent.name == name:
            return skill_md.parent
    return None


def _validate_file_path(file_path: str) -> str | None:
    if not file_path:
        return "file_path is required."
    normalized = Path(file_path)
    if ".." in normalized.parts:
        return "Path traversal ('..') is not allowed."
    if not normalized.parts or normalized.parts[0] not in ALLOWED_SUBDIRS:
        allowed = ", ".join(sorted(ALLOWED_SUBDIRS))
        return f"File must be under one of: {allowed}. Got: '{file_path}'"
    if len(normalized.parts) < 2:
        return f"Provide a file path, not just a directory."
    return None


def _atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.tmp.",
        suffix="",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def action_create(name: str, content: str, category: str | None = None, local: bool = False) -> dict:
    err = _validate_name(name)
    if err:
        return {"success": False, "error": err}
    err = _validate_frontmatter(content)
    if err:
        return {"success": False, "error": err}
    err = _validate_content_size(content)
    if err:
        return {"success": False, "error": err}

    skill_dir = _resolve_skill_dir(name, category, local=local)
    if skill_dir.exists():
        return {"success": False, "error": f"Skill '{name}' already exists at {skill_dir}"}

    skill_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ALLOWED_SUBDIRS:
        (skill_dir / subdir).mkdir(exist_ok=True)

    skill_md = skill_dir / "SKILL.md"
    try:
        _atomic_write_text(skill_md, content)
    except Exception as e:
        return {"success": False, "error": f"Failed to write SKILL.md: {e}"}

    return {"success": True, "message": f"Created skill '{name}' at {skill_dir}", "path": str(skill_dir)}


def action_edit(name: str, content: str, local: bool = False) -> dict:
    err = _validate_name(name)
    if err:
        return {"success": False, "error": err}
    err = _validate_frontmatter(content)
    if err:
        return {"success": False, "error": err}
    err = _validate_content_size(content)
    if err:
        return {"success": False, "error": err}

    skill_dir = _find_skill(name, local=local)
    if not skill_dir:
        # try global if local not found
        if local:
            skill_dir = _find_skill(name, local=False)
        if not skill_dir:
            return {"success": False, "error": f"Skill '{name}' not found."}

    skill_md = skill_dir / "SKILL.md"
    backup = skill_dir / ".SKILL.md.bak"
    try:
        if skill_md.exists():
            shutil.copy2(skill_md, backup)
        _atomic_write_text(skill_md, content)
    except Exception as e:
        if backup.exists():
            shutil.copy2(backup, skill_md)
        return {"success": False, "error": f"Failed to edit SKILL.md: {e}"}
    finally:
        if backup.exists():
            os.unlink(backup)

    return {"success": True, "message": f"Updated skill '{name}'"}


def action_patch(name: str, old_string: str, new_string: str, file_path: str | None = None, local: bool = False) -> dict:
    skill_dir = _find_skill(name, local=local)
    if not skill_dir:
        if local:
            skill_dir = _find_skill(name, local=False)
        if not skill_dir:
            return {"success": False, "error": f"Skill '{name}' not found."}

    target = skill_dir / (file_path or "SKILL.md")
    if file_path:
        err = _validate_file_path(file_path)
        if err:
            return {"success": False, "error": err}

    if not target.exists():
        return {"success": False, "error": f"File not found: {target}"}

    try:
        original = target.read_text(encoding="utf-8")
    except Exception as e:
        return {"success": False, "error": f"Failed to read file: {e}"}

    if old_string not in original:
        return {"success": False, "error": "old_string not found in file."}

    updated = original.replace(old_string, new_string, 1)
    if updated == original:
        return {"success": False, "error": "Patch did not change anything."}

    if target.name == "SKILL.md":
        err = _validate_frontmatter(updated)
        if err:
            return {"success": False, "error": f"Patch would break frontmatter: {err}"}
        err = _validate_content_size(updated)
        if err:
            return {"success": False, "error": err}

    try:
        _atomic_write_text(target, updated)
    except Exception as e:
        return {"success": False, "error": f"Failed to write patched file: {e}"}

    return {"success": True, "message": f"Patched skill '{name}'"}


def action_write_file(name: str, file_path: str, content: str, local: bool = False) -> dict:
    err = _validate_file_path(file_path)
    if err:
        return {"success": False, "error": err}
    if len(content.encode("utf-8")) > MAX_SKILL_FILE_BYTES:
        return {"success": False, "error": f"File exceeds {MAX_SKILL_FILE_BYTES} bytes."}

    skill_dir = _find_skill(name, local=local)
    if not skill_dir:
        if local:
            skill_dir = _find_skill(name, local=False)
        if not skill_dir:
            return {"success": False, "error": f"Skill '{name}' not found."}

    target = skill_dir / file_path
    try:
        _atomic_write_text(target, content)
    except Exception as e:
        return {"success": False, "error": f"Failed to write file: {e}"}

    return {"success": True, "message": f"Wrote {file_path} for skill '{name}'"}


def action_remove_file(name: str, file_path: str, local: bool = False) -> dict:
    err = _validate_file_path(file_path)
    if err:
        return {"success": False, "error": err}

    skill_dir = _find_skill(name, local=local)
    if not skill_dir:
        if local:
            skill_dir = _find_skill(name, local=False)
        if not skill_dir:
            return {"success": False, "error": f"Skill '{name}' not found."}

    target = skill_dir / file_path
    if not target.exists():
        return {"success": False, "error": f"File not found: {target}"}

    try:
        target.unlink()
    except Exception as e:
        return {"success": False, "error": f"Failed to remove file: {e}"}

    return {"success": True, "message": f"Removed {file_path} from skill '{name}'"}


def action_delete(name: str, local: bool = False) -> dict:
    skill_dir = _find_skill(name, local=local)
    if not skill_dir:
        if local:
            skill_dir = _find_skill(name, local=False)
        if not skill_dir:
            return {"success": False, "error": f"Skill '{name}' not found."}

    try:
        shutil.rmtree(skill_dir)
    except Exception as e:
        return {"success": False, "error": f"Failed to delete skill: {e}"}

    return {"success": True, "message": f"Deleted skill '{name}'"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Skill Manager for Claude Code")
    subparsers = parser.add_subparsers(dest="action", required=True)

    create_p = subparsers.add_parser("create")
    create_p.add_argument("--name", required=True)
    create_p.add_argument("--content", required=True)
    create_p.add_argument("--category")
    create_p.add_argument("--local", action="store_true", help="Store in current project's .claude/skills/")

    edit_p = subparsers.add_parser("edit")
    edit_p.add_argument("--name", required=True)
    edit_p.add_argument("--content", required=True)
    edit_p.add_argument("--local", action="store_true")

    patch_p = subparsers.add_parser("patch")
    patch_p.add_argument("--name", required=True)
    patch_p.add_argument("--old-string", required=True)
    patch_p.add_argument("--new-string", required=True)
    patch_p.add_argument("--file-path")
    patch_p.add_argument("--local", action="store_true")

    write_p = subparsers.add_parser("write_file")
    write_p.add_argument("--name", required=True)
    write_p.add_argument("--file-path", required=True)
    write_p.add_argument("--content", required=True)
    write_p.add_argument("--local", action="store_true")

    remove_p = subparsers.add_parser("remove_file")
    remove_p.add_argument("--name", required=True)
    remove_p.add_argument("--file-path", required=True)
    remove_p.add_argument("--local", action="store_true")

    delete_p = subparsers.add_parser("delete")
    delete_p.add_argument("--name", required=True)
    delete_p.add_argument("--local", action="store_true")

    args = parser.parse_args()
    action = args.action
    local = getattr(args, "local", False)

    if action == "create":
        result = action_create(args.name, args.content, category=getattr(args, "category", None), local=local)
    elif action == "edit":
        result = action_edit(args.name, args.content, local=local)
    elif action == "patch":
        result = action_patch(args.name, args.old_string, args.new_string, file_path=getattr(args, "file_path", None), local=local)
    elif action == "write_file":
        result = action_write_file(args.name, args.file_path, args.content, local=local)
    elif action == "remove_file":
        result = action_remove_file(args.name, args.file_path, local=local)
    elif action == "delete":
        result = action_delete(args.name, local=local)
    else:
        result = {"success": False, "error": "Unknown action"}

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())

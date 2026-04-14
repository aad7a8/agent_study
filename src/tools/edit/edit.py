import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import safe_path


def _exact_replace(content: str, old: str, new: str, replace_all: bool) -> str | None:
    count = content.count(old)
    if count == 0:
        return None
    if not replace_all and count > 1:
        raise ValueError(
            f"Found {count} occurrences of old_string. "
            "Provide more surrounding context to make it unique, or use replace_all=True."
        )
    return content.replace(old, new) if replace_all else content.replace(old, new, 1)


def _line_trimmed_replace(content: str, old: str, new: str) -> str | None:
    content_lines = content.splitlines(keepends=True)
    old_lines = old.splitlines()
    if not old_lines:
        return None
    old_trimmed = [l.strip() for l in old_lines]
    n = len(old_lines)
    for i in range(len(content_lines) - n + 1):
        chunk = [l.rstrip("\r\n").strip() for l in content_lines[i : i + n]]
        if chunk == old_trimmed:
            before = "".join(content_lines[:i])
            after = "".join(content_lines[i + n :])
            return before + new + after
    return None


def _whitespace_normalized_replace(content: str, old: str, new: str) -> str | None:
    def normalize(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    norm_old = normalize(old)
    content_lines = content.splitlines(keepends=True)
    n = len(old.splitlines())
    for i in range(len(content_lines) - n + 1):
        chunk = "".join(content_lines[i : i + n])
        if normalize(chunk) == norm_old:
            before = "".join(content_lines[:i])
            after = "".join(content_lines[i + n :])
            return before + new + after
    return None


def edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    try:
        safe = safe_path(file_path)
    except ValueError as e:
        return f"Security error: {e}"

    if not os.path.exists(safe):
        return f"File not found: {file_path}"

    with open(safe, "r", encoding="utf-8") as f:
        content = f.read()

    new_content = None

    try:
        new_content = _exact_replace(content, old_string, new_string, replace_all)
    except ValueError as e:
        return str(e)

    if new_content is None:
        new_content = _line_trimmed_replace(content, old_string, new_string)

    if new_content is None:
        new_content = _whitespace_normalized_replace(content, old_string, new_string)

    if new_content is None:
        return (
            "Error: Could not find old_string in file. "
            "Check for exact match issues (indentation, whitespace, line endings)."
        )

    with open(safe, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"Edit applied to {file_path}"

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import is_sensitive, safe_path


def write(path: str, content: str) -> str:
    try:
        safe = safe_path(path)
    except ValueError as e:
        return f"Security error: {e}"

    if is_sensitive(safe):
        return (
            f"Sensitive file blocked: '{path}' matches a known sensitive file pattern. "
            f"Writing to this file is not allowed."
        )

    parent = os.path.dirname(safe)

    # Ensure the parent directory is also within the allowed base before creating it.
    try:
        safe_path(parent)
    except ValueError as e:
        return f"Security error (parent directory): {e}"

    os.makedirs(parent, exist_ok=True)

    with open(safe, "w", encoding="utf-8") as f:
        f.write(content)

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return f"Written {line_count} lines to {path}"

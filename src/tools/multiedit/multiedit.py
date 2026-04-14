import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools.edit.edit import edit


def multiedit(file_path: str, edits: list[dict]) -> str:
    results = []
    for i, e in enumerate(edits):
        result = edit(
            file_path=file_path,
            old_string=e["old_string"],
            new_string=e["new_string"],
            replace_all=e.get("replace_all", False),
        )
        results.append(result)
        if result.startswith("Error") or result.startswith("File not found"):
            return f"Stopped at edit {i + 1}/{len(edits)}: {result}"

    return results[-1] if results else "No edits provided"

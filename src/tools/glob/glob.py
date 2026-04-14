import os
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import safe_path


def glob(pattern: str, directory: str = ".") -> list[str]:
    try:
        safe_dir = safe_path(directory)
    except ValueError as e:
        return [f"Security error: {e}"]

    result = subprocess.run(
        ["rg", "--files", "--glob", pattern, safe_dir],
        capture_output=True,
        text=True,
    )

    files = [f for f in result.stdout.strip().splitlines() if f]

    files_with_mtime = []
    for f in files:
        try:
            mtime = os.path.getmtime(f)
        except OSError:
            mtime = 0
        files_with_mtime.append((f, mtime))

    files_with_mtime.sort(key=lambda x: x[1], reverse=True)
    truncated = len(files_with_mtime) > 100
    results = [f for f, _ in files_with_mtime[:100]]

    if truncated:
        results.append(f"(truncated: {len(files_with_mtime) - 100} more results not shown)")

    return results

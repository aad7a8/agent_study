import os
import subprocess
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import safe_path


def grep(pattern: str, path: str = ".", glob_pattern: str = None) -> list[dict]:
    try:
        safe = safe_path(path)
    except ValueError as e:
        return [{"error": str(e)}]

    cmd = [
        "rg",
        "-nH",
        "--hidden",
        "--no-messages",
        "--field-match-separator=|",
        "--regexp",
        pattern,
    ]
    if glob_pattern:
        cmd += ["--glob", glob_pattern]
    cmd.append(safe)

    result = subprocess.run(cmd, capture_output=True, text=True)

    matches = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            filepath, lineno, content = parts
            matches.append(
                {
                    "file": filepath,
                    "line": int(lineno) if lineno.isdigit() else lineno,
                    "content": content,
                }
            )

    file_mtimes: dict[str, float] = {}
    for m in matches:
        f = m["file"]
        if f not in file_mtimes:
            try:
                file_mtimes[f] = os.path.getmtime(f)
            except OSError:
                file_mtimes[f] = 0

    matches.sort(key=lambda m: file_mtimes.get(m["file"], 0), reverse=True)
    return matches[:100]

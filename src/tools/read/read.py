import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import is_sensitive, safe_path

BINARY_EXTENSIONS = {
    ".zip", ".exe", ".pyc", ".so", ".dll", ".bin", ".tar", ".gz", ".bz2",
    ".7z", ".rar", ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flac", ".wav",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".db", ".sqlite", ".class", ".o", ".a", ".lib",
}


def _is_binary(path: str, raw: bytes) -> bool:
    if os.path.splitext(path)[1].lower() in BINARY_EXTENSIONS:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return (len(sample) - printable) / len(sample) > 0.30


def read(path: str, offset: int = 1, limit: int = 2000) -> str:
    try:
        safe = safe_path(path)
    except ValueError as e:
        return f"Security error: {e}"

    if is_sensitive(safe):
        return (
            f"Sensitive file blocked: '{path}' matches a known sensitive file pattern "
            f"(.env, private keys, credentials). Access denied."
        )

    if not os.path.exists(safe):
        parent = os.path.dirname(safe)
        name = os.path.basename(safe).lower()
        similar = []
        try:
            for entry in os.listdir(parent):
                el = entry.lower()
                if name in el or el in name:
                    similar.append(entry)
        except OSError:
            pass
        msg = f"File not found: {path}"
        if similar:
            msg += f"\nDid you mean: {', '.join(similar[:3])}?"
        return msg

    if os.path.isdir(safe):
        entries = []
        try:
            for entry in sorted(os.listdir(safe)):
                full = os.path.join(safe, entry)
                entries.append(entry + "/" if os.path.isdir(full) else entry)
        except OSError:
            pass
        return "\n".join(entries) if entries else "(empty directory)"

    try:
        with open(safe, "rb") as f:
            raw = f.read()
    except OSError as e:
        return f"Error reading file: {e}"

    if _is_binary(safe, raw):
        return f"Binary file ({len(raw)} bytes): {path}"

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    lines = text.splitlines()
    start = max(0, offset - 1)
    end = start + limit
    selected = lines[start:end]

    output = [f"{start + i + 1}: {line}" for i, line in enumerate(selected)]
    if end < len(lines):
        output.append(f"... ({len(lines) - end} more lines, use offset={end + 1} to continue)")

    return "\n".join(output)

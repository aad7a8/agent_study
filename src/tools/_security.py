"""
Shared path-safety utilities for all file-touching tools.

safe_path(path, base) resolves a path and verifies it stays within `base`.
Blocks three common attack vectors:
  1. Null-byte injection  ("../../etc/passwd\x00.txt")
  2. Directory traversal  ("../../etc/passwd")
  3. Symlink escape       (a symlink inside base pointing outside base)
"""

import os

# Configurable via environment; defaults to CWD at first import.
BASE_DIR: str = os.path.abspath(os.environ.get("TOOL_BASE_DIR", "") or os.getcwd())


def safe_path(path: str, base: str | None = None) -> str:
    """
    Return the absolute, normalised path if it is inside `base`.
    Raises ValueError otherwise.
    """
    if "\x00" in path:
        raise ValueError("Path contains a null byte — rejected.")

    resolved_base = os.path.abspath(base or BASE_DIR)

    # Step 1: normalise (collapses '..' without hitting the filesystem)
    abs_path = os.path.normpath(os.path.join(resolved_base, path)
                                if not os.path.isabs(path)
                                else path)

    def _within(p: str, b: str) -> bool:
        b = b.rstrip(os.sep) + os.sep
        return p.startswith(b) or p == b.rstrip(os.sep)

    if not _within(abs_path, resolved_base):
        raise ValueError(
            f"Path traversal blocked: '{path}' resolves to '{abs_path}' "
            f"which is outside the allowed base '{resolved_base}'."
        )

    # Step 2: resolve symlinks and re-check (prevents symlink escape)
    if os.path.exists(abs_path):
        real_path = os.path.realpath(abs_path)
        real_base = os.path.realpath(resolved_base)
        if not _within(real_path, real_base):
            raise ValueError(
                f"Symlink escape blocked: '{path}' resolves via symlink to "
                f"'{real_path}' which is outside the allowed base '{real_base}'."
            )

    return abs_path


SENSITIVE_PATTERNS = (
    ".env",
    ".env.",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    "credentials",
    "secret",
)


def is_sensitive(path: str) -> bool:
    """Return True if the filename matches known sensitive file patterns."""
    name = os.path.basename(path).lower()
    return any(pat in name for pat in SENSITIVE_PATTERNS)

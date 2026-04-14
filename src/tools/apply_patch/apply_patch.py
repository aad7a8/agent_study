import os
import sys
from dataclasses import dataclass, field

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools._security import safe_path


@dataclass
class _Hunk:
    context: str
    removes: list[str] = field(default_factory=list)
    adds: list[str] = field(default_factory=list)


@dataclass
class _Op:
    op: str  # "add" | "update" | "delete"
    path: str
    new_path: str | None = None
    content: list[str] = field(default_factory=list)
    hunks: list[_Hunk] = field(default_factory=list)


def _parse(patch: str) -> list[_Op]:
    lines = patch.splitlines()
    i = 0
    while i < len(lines) and lines[i].strip() != "*** Begin Patch":
        i += 1
    i += 1

    ops: list[_Op] = []
    current: _Op | None = None
    current_hunk: _Hunk | None = None

    def _flush():
        nonlocal current, current_hunk
        if current_hunk and current and current.op == "update":
            current.hunks.append(current_hunk)
            current_hunk = None
        if current:
            ops.append(current)
            current = None

    while i < len(lines):
        line = lines[i]

        if line.strip() == "*** End Patch":
            _flush()
            break

        if line.startswith("*** Add File:"):
            _flush()
            current = _Op(op="add", path=line[len("*** Add File:"):].strip())
        elif line.startswith("*** Update File:"):
            _flush()
            current = _Op(op="update", path=line[len("*** Update File:"):].strip())
        elif line.startswith("*** Delete File:"):
            _flush()
            ops.append(_Op(op="delete", path=line[len("*** Delete File:"):].strip()))
        elif line.startswith("*** Move to:") and current and current.op == "update":
            current.new_path = line[len("*** Move to:"):].strip()
        elif line.startswith("@@") and current and current.op == "update":
            if current_hunk:
                current.hunks.append(current_hunk)
            current_hunk = _Hunk(context=line[2:].strip())
        elif current:
            if current.op == "add" and (line.startswith("+") or line.startswith(" ")):
                current.content.append(line[1:])
            elif current.op == "update" and current_hunk is not None:
                if line.startswith("-"):
                    current_hunk.removes.append(line[1:])
                elif line.startswith("+"):
                    current_hunk.adds.append(line[1:])

        i += 1

    return ops


def _apply_hunk(lines: list[str], hunk: _Hunk) -> list[str]:
    anchor = -1
    if hunk.context:
        for idx, line in enumerate(lines):
            if line.rstrip("\r\n").strip() == hunk.context.strip():
                anchor = idx
                break
        if anchor == -1:
            raise ValueError(f"Context line not found: {repr(hunk.context)}")

    start = anchor + 1
    result = list(lines[:start])
    rest = list(lines[start:])

    consumed = 0
    for remove_text in [r.rstrip("\r\n") for r in hunk.removes]:
        for j in range(consumed, len(rest)):
            if rest[j].rstrip("\r\n") == remove_text:
                result.extend(rest[consumed:j])
                consumed = j + 1
                break

    result.extend(rest[consumed:])

    for add_line in reversed(hunk.adds):
        insert = add_line if add_line.endswith("\n") else add_line + "\n"
        result.insert(start, insert)

    return result


def apply_patch(patch: str) -> str:
    ops = _parse(patch)
    results = []

    for op in ops:
        # Validate all paths before touching the filesystem.
        try:
            safe = safe_path(op.path)
            safe_dest = safe_path(op.new_path) if op.new_path else safe
        except ValueError as e:
            results.append(f"Security error: {e}")
            continue

        if op.op == "add":
            os.makedirs(os.path.dirname(safe), exist_ok=True)
            with open(safe, "w", encoding="utf-8") as f:
                f.write("\n".join(op.content))
            results.append(f"Added: {op.path}")

        elif op.op == "delete":
            if os.path.exists(safe):
                os.remove(safe)
                results.append(f"Deleted: {op.path}")
            else:
                results.append(f"Skipped (not found): {op.path}")

        elif op.op == "update":
            if not os.path.exists(safe):
                results.append(f"Error: file not found: {op.path}")
                continue

            with open(safe, "r", encoding="utf-8") as f:
                lines = f.readlines()

            try:
                for hunk in op.hunks:
                    lines = _apply_hunk(lines, hunk)
            except ValueError as e:
                results.append(f"Error in {op.path}: {e}")
                continue

            os.makedirs(os.path.dirname(safe_dest), exist_ok=True)
            with open(safe_dest, "w", encoding="utf-8") as f:
                f.writelines(lines)

            if op.new_path:
                os.remove(safe)
                results.append(f"Updated + moved: {op.path} → {op.new_path}")
            else:
                results.append(f"Updated: {op.path}")

    return "\n".join(results) if results else "No operations applied"

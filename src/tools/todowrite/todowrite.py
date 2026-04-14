_VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
_STATUS_SYMBOLS = {
    "pending": "○",
    "in_progress": "◎",
    "completed": "✓",
    "cancelled": "✗",
}

_todos: list[dict] = []


def todowrite(todos: list[dict]) -> str:
    global _todos

    for todo in todos:
        if "id" not in todo or "content" not in todo:
            return "Error: each todo must have 'id' and 'content' fields"
        status = todo.get("status", "pending")
        if status not in _VALID_STATUSES:
            return f"Error: invalid status '{status}'. Must be one of: {', '.join(_VALID_STATUSES)}"

    in_progress = [t for t in todos if t.get("status") == "in_progress"]
    if len(in_progress) > 1:
        return "Error: only one todo can be 'in_progress' at a time"

    _todos = [
        {
            "id": t["id"],
            "content": t["content"],
            "status": t.get("status", "pending"),
        }
        for t in todos
    ]

    if not _todos:
        return "Todo list cleared"

    lines = [
        f"{_STATUS_SYMBOLS.get(t['status'], '?')} [{t['id']}] {t['content']} ({t['status']})"
        for t in _todos
    ]
    return "Todo list:\n" + "\n".join(lines)


def todoread() -> str:
    if not _todos:
        return "Todo list is empty"
    lines = [
        f"{_STATUS_SYMBOLS.get(t['status'], '?')} [{t['id']}] {t['content']} ({t['status']})"
        for t in _todos
    ]
    return "Todo list:\n" + "\n".join(lines)

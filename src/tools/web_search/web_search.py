from ddgs import DDGS


def web_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        results = DDGS().text(query, max_results=max_results)
        return results or []
    except Exception as e:
        return [{"error": str(e)}]

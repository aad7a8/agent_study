from ddgs import DDGS


def web_fetch(url: str, fmt: str = "text_markdown") -> dict:
    try:
        result = DDGS().extract(url, fmt=fmt)
        return result
    except Exception as e:
        return {"url": url, "error": str(e), "content": ""}

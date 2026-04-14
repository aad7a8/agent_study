from ddgs import DDGS


def web_fetch(url: str, fmt: str = "text_markdown") -> dict:
    result = DDGS().extract(url, fmt=fmt)
    return result

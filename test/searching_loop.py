import json
import os
import sys

from dotenv import load_dotenv
from litellm import completion

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.tools.web_fetch.web_fetch import web_fetch
from src.tools.web_search.web_search import web_search

load_dotenv()

MODEL = "gpt-4o-mini"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DDGS and return a list of results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and extract its content. Use this to read a full webpage after finding it via web_search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch and extract content from.",
                    },
                    "fmt": {
                        "type": "string",
                        "description": 'Output format: "text_markdown" (default), "text_plain", "text_rich", or "text" (raw HTML).',
                        "default": "text_markdown",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to web_search and web_fetch tools. "
    "Use web_search to find relevant URLs, then web_fetch to read full page content when needed."
)


TOOL_MAP = {
    "web_search": web_search,
    "web_fetch": web_fetch,
}


def run_tool_call(tool_call) -> str:
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    result = TOOL_MAP[name](**args)
    return json.dumps(result, ensure_ascii=False)


def chat(messages: list[dict]) -> str:
    while True:
        response = completion(model=MODEL, messages=messages, tools=TOOLS)
        message = response.choices[0].message
        print(response)
        print(message.tool_calls)

        if not message.tool_calls:
            return message.content

        messages.append(message)

        for tool_call in message.tool_calls:
            result = run_tool_call(tool_call)
            print(result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )


def main():
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    print("Searching loop started. Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() == "exit":
            break

        messages.append({"role": "user", "content": user_input})

        reply = chat(messages)
        messages.append({"role": "assistant", "content": reply})

        print(f"\nAssistant: {reply}\n")


if __name__ == "__main__":
    main()

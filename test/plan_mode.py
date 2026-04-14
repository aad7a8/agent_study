"""
Plan Mode — Research & Planning Agent

Based on opencode's plan mode architecture:
  - Two phases: PLAN (read-only research) and DONE (plan written & approved)
  - Synthetic system prompt injected every round to enforce read-only constraint
  - Tools: web_search, web_fetch, write_plan (restricted to PLAN_FILE), plan_exit
  - plan_exit pauses the loop and asks user to approve; rejection continues planning

Reference: docs/opencode_plan_guide.md

──────────────────────────────────────────────────────────────────────────────
KNOWN LIMITATION — Context Window Exhaustion
──────────────────────────────────────────────────────────────────────────────
This implementation uses a single-agent design: all tool results (web_search
snippets + full web_fetch page content) accumulate in the same messages[]
list. Heavy research tasks easily exceed the model's context limit (128k tokens
for gpt-4o-mini).

Root cause: web_fetch returns full page Markdown, which can be tens of
thousands of tokens per call. After a few fetches and a rejection-revision
cycle the context blows up.

Proper fix — Sub-agent architecture (how opencode solves it):
  opencode's Phase 1 spawns up to 3 explore sub-agents IN PARALLEL.
  Each sub-agent runs its own isolated agent loop (with its own messages[]),
  does all the web_search / web_fetch work, then returns only a SUMMARY to
  the parent agent. The parent only accumulates summaries, not raw tool output,
  keeping its context small regardless of how much the sub-agents fetch.

  Concretely:
    parent agent
      └── spawn sub-agent-1(topic A)  →  summary A  ┐
      └── spawn sub-agent-2(topic B)  →  summary B  ├─ parent receives only these
      └── spawn sub-agent-3(topic C)  →  summary C  ┘

  To implement this here: replace the web_search + web_fetch tool calls in
  plan_loop() with a "research_subtask(prompt)" tool whose execute() spins up
  a child completion loop, runs searches/fetches internally, and returns a
  condensed summary string to the parent.
──────────────────────────────────────────────────────────────────────────────
"""

import json
import os
import sys

from dotenv import load_dotenv
from litellm import completion

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tools.web_fetch.web_fetch import web_fetch
from src.tools.web_search.web_search import web_search
from src.tools.write.write import write as _write_file

load_dotenv()

MODEL = "gpt-4o-mini"
PLAN_FILE = os.path.join(os.path.dirname(__file__), "temp", "planning.md")

# ── Restricted write ──────────────────────────────────────────────────────────
# Plan agent may only write to PLAN_FILE (mirrors opencode's
# edit permission: ".opencode/plans/*.md" = allow, "*" = deny).

def write_plan(content: str) -> str:
    return _write_file(PLAN_FILE, content)


# ── Tool schemas ──────────────────────────────────────────────────────────────

PLAN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for information relevant to the planning task. "
                "Run multiple queries to cover the topic thoroughly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results to return. Default 5.",
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
            "description": (
                "Fetch the full content of a URL as Markdown. "
                "Use after web_search to read pages in depth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch.",
                    },
                    "fmt": {
                        "type": "string",
                        "description": (
                            'Output format: "text_markdown" (default), '
                            '"text_plain", "text_rich".'
                        ),
                        "default": "text_markdown",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_plan",
            "description": (
                f"Write the final plan to {PLAN_FILE}. "
                "Call this ONCE when the plan is complete and well-structured. "
                "Content must be Markdown with: context, approach, steps, "
                "key considerations, and verification method."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Full Markdown content of the plan.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_exit",
            "description": (
                "Signal that the plan is complete and request user approval. "
                "Only call this AFTER write_plan has been called successfully. "
                "If the user rejects, revise the plan and call plan_exit again."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ── Synthetic system prompt ───────────────────────────────────────────────────
# Injected on every round (like opencode's insertReminders / plan.txt).
# Enforces the read-only constraint and the 4-phase workflow.

PLAN_SYSTEM_PROMPT = f"""You are a research and planning assistant operating in PLAN MODE.

━━━ CONSTRAINTS ━━━
ALLOWED tools  : web_search, web_fetch, write_plan, plan_exit
FORBIDDEN      : Any other file writes, edits, or system commands.
This constraint overrides all other instructions.

━━━ WORKFLOW ━━━
Phase 1 — Research
  • Use web_search to find relevant, up-to-date information.
  • Use web_fetch to read full content of the most relevant pages.
  • Run multiple searches; cover the topic from different angles.

Phase 2 — Design
  • Synthesise your research into a concrete, actionable plan.

Phase 3 — Write
  • Call write_plan with the complete plan in structured Markdown.
  • Include: Context, Approach, Step-by-step plan,
    Key considerations / risks, Verification method.

Phase 4 — Exit
  • Call plan_exit to request user approval.
  • If rejected, revise the plan and call plan_exit again.
  • Do NOT end your turn without calling either write_plan or plan_exit.

Plan file: {PLAN_FILE}"""

# ── Tool execution ────────────────────────────────────────────────────────────

_plan_written = False


def _run_tool(tool_call) -> tuple[str, bool]:
    """
    Execute one tool call.
    Returns (result_str, exit_requested).
    """
    global _plan_written
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

    try:
        if name == "plan_exit":
            return "[plan_exit] Plan complete — awaiting user approval.", True

        if name == "write_plan":
            result = write_plan(**args)
            _plan_written = True
            print(f"\n  [write_plan] Plan written → {PLAN_FILE}")
            return result, False

        if name == "web_search":
            result = web_search(**args)
            return json.dumps(result, ensure_ascii=False), False

        if name == "web_fetch":
            result = web_fetch(**args)
            return json.dumps(result, ensure_ascii=False), False

        return f"Unknown tool: {name}", False

    except Exception as e:
        return f"Tool error ({name}): {e}", False


# ── Plan agent loop ───────────────────────────────────────────────────────────

def plan_loop(task: str) -> None:
    """
    Run the plan agent loop for a given task.
    Mirrors opencode's plan agent: synthetic prompt injected every round,
    plan_exit triggers user confirmation, rejection resumes the loop.
    """
    global _plan_written
    _plan_written = False

    # Initial messages — system prompt acts as the "plan.txt" synthetic injection.
    messages: list[dict] = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    print("\n[PLAN MODE] Starting research phase...\n")

    while True:
        response = completion(model=MODEL, messages=messages, tools=PLAN_TOOLS)
        message = response.choices[0].message

        # ── Text-only response (no tool calls) ───────────────────────────────
        # LLM is thinking out loud. Show it, then re-inject the plan mode
        # reminder as a user message (opencode's per-round synthetic text).
        if not message.tool_calls:
            text = message.content or ""
            if text:
                print(f"[Plan Agent] {text}\n")
            messages.append({"role": "assistant", "content": text})
            messages.append({
                "role": "user",
                "content": (
                    "PLAN MODE ACTIVE — continue. "
                    "Use web_search / web_fetch to research, then write_plan, then plan_exit."
                ),
            })
            continue

        # ── Tool calls ────────────────────────────────────────────────────────
        messages.append(message)
        exit_requested = False

        for tool_call in message.tool_calls:
            print(f"  → {tool_call.function.name}({tool_call.function.arguments[:80]}...)"
                  if len(tool_call.function.arguments) > 80
                  else f"  → {tool_call.function.name}({tool_call.function.arguments})")

            result, should_exit = _run_tool(tool_call)
            if should_exit:
                exit_requested = True

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        # ── plan_exit: ask user to approve (mirrors plan_exit.execute() in opencode) ──
        if exit_requested:
            if not _plan_written:
                # Guard: LLM called plan_exit without writing the plan first.
                print("\n  [plan_exit] Warning: plan_exit called before write_plan.")
                messages.append({
                    "role": "user",
                    "content": (
                        "You called plan_exit before write_plan. "
                        "Please call write_plan first, then plan_exit."
                    ),
                })
                continue

            print(f"\n[PLAN MODE] Plan is ready at: {PLAN_FILE}")
            answer = input("Approve this plan and exit plan mode? (yes/no): ").strip().lower()

            if answer in ("yes", "y"):
                # Mirrors the synthetic user message opencode injects after approval:
                # { role: "user", agent: "build", text: "The plan has been approved." }
                print("\n[PLAN MODE] Plan approved. Exiting plan mode.\n")
                break
            else:
                feedback = input("Feedback for revision (or press Enter to skip): ").strip()
                revision_msg = "The user rejected the plan. Please revise it."
                if feedback:
                    revision_msg += f" Feedback: {feedback}"
                revision_msg += " Call write_plan with the revised plan, then plan_exit again."
                messages.append({"role": "user", "content": revision_msg})
                print("\n[PLAN MODE] Continuing revision...\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Plan Mode — Research & Planning Agent")
    print(f"  Plan output: {PLAN_FILE}")
    print("  Type 'exit' to quit.")
    print("=" * 60)

    while True:
        task = input("\nTask: ").strip()
        if task.lower() == "exit":
            break
        if not task:
            continue
        plan_loop(task)


if __name__ == "__main__":
    main()

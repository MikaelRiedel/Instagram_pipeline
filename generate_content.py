"""
Instagram carousel content generator.

What this does:
  1. Asks Claude to hunt for a recently launched/updated AI tool (using live
     web search), actively avoiding stale, overexposed picks and anything
     already covered (see history.json)
  2. Asks Claude to write a caption + carousel slides, then critiques and
     rewrites its own draft for specificity and punch
  3. Renders each slide as a designed image with Pillow
  4. Saves everything under posts/<date>/ -- nothing is uploaded, posted,
     or pushed to GitHub by this script. That's publish_to_buffer.py.

Setup (once):
    pip install anthropic pillow requests
    export ANTHROPIC_API_KEY="your-key-here"

Optional but recommended: put a *-Bold.ttf and a *-Regular.ttf font file
(e.g. Inter, free from https://fonts.google.com/specimen/Inter) into
assets/fonts/ next to this script.

Run:
    python3 generate_content.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

import anthropic

from pipeline_common import (
    HISTORY_PATH,
    MODEL,
    POSTS_DIR,
    POST_SCHEMA,
    SCRIPT_DIR,
    WRITING_RULES,
    render_all_slides,
)

# Well-known incumbents to steer away from by default -- the point of this
# account is surfacing new/interesting tools, not re-reviewing ChatGPT for
# the hundredth time. Claude can still override this if there's a genuinely
# newsworthy angle (a major new feature/version), per the prompt below.
OVEREXPOSED_TOOLS = [
    "ChatGPT", "Claude", "Gemini", "Microsoft Copilot", "GitHub Copilot",
    "Notion AI", "Grammarly", "Jasper", "Copy.ai", "Midjourney", "Canva Magic Studio",
]


def load_history() -> list[str]:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


def save_history(history: list[str]) -> None:
    HISTORY_PATH.write_text(json.dumps(history, indent=2))


def research_topic(client: anthropic.Anthropic, history: list[str]) -> str:
    avoid_list = ", ".join(history) if history else "(none yet -- this is the first post)"
    today_str = date.today().strftime("%B %d, %Y")
    overexposed = ", ".join(OVEREXPOSED_TOOLS)

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
        messages=[{
            "role": "user",
            "content": (
                f"Today's date is {today_str}. You're researching content for a daily "
                "Instagram account that reviews AI tools and productivity software for an "
                "audience that already knows the famous names -- they follow this account "
                "specifically to hear about things they HAVEN'T seen everywhere else. "
                "Monetized via affiliate links, so credibility matters more than hype.\n\n"
                "Your job: find ONE AI tool or productivity app that is genuinely fresh -- "
                "launched, out of beta, or majorly updated within roughly the last 1-3 "
                "months. Use web search actively for this: search things like 'new AI tool "
                f"launch {date.today().strftime('%B %Y')}', 'Product Hunt AI tool of the "
                "day', 'AI startup launches this week', 'new AI tool Y Combinator', rather "
                "than just describing a tool from memory.\n\n"
                f"Do NOT pick: {overexposed} (too oversaturated to be interesting) -- unless "
                "you find a genuinely newsworthy angle, like a brand-new flagship feature or "
                "version launch, in which case name that specific angle explicitly.\n"
                f"Do NOT repeat any of these already-covered topics: {avoid_list}.\n\n"
                "Once you've found a good candidate, confirm via web search: its current "
                "pricing tiers, its 3-5 most useful features, roughly when it launched/was "
                "last updated (state this explicitly), and at least one honest limitation or "
                "downside -- don't only write positives, credibility matters more than hype.\n\n"
                "Reply with a plain-text research summary covering: tool name, why it's "
                "timely/new right now, pricing, features, the limitation, and the source "
                "URLs you used."
            ),
        }],
    )

    return "".join(b.text for b in response.content if b.type == "text")


def write_post(client: anthropic.Anthropic, research: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                "Using this research, write an Instagram carousel post for a business "
                "account reviewing AI tools and productivity software:\n\n"
                f"{research}\n\n{WRITING_RULES}"
            ),
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def critique_and_revise(client: anthropic.Anthropic, draft: dict) -> dict:
    """Self-critique pass: a first draft from an LLM is reliably generic.
    This forces Claude to find its own clichés/vagueness and fix them."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                "Here is a draft Instagram post:\n\n"
                f"{json.dumps(draft, indent=2)}\n\n"
                "Critique it harshly: flag every generic phrase, vague marketing "
                "adjective ('powerful', 'game-changing', 'seamless', etc.), weak or boring "
                "hook, and any slide that doesn't carry one specific, concrete point. Then "
                "rewrite the whole post to fix every issue you found -- same tool, same "
                "facts, but substantially punchier, more specific, and more scroll-stopping. "
                f"{WRITING_RULES}\n"
                "Return ONLY the improved, rewritten version in the schema -- not the "
                "critique itself."
            ),
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print('Set ANTHROPIC_API_KEY first, e.g.:\n  export ANTHROPIC_API_KEY="your-key-here"')
        sys.exit(1)

    client = anthropic.Anthropic()
    history = load_history()

    print("Researching a fresh topic (this calls the web -- may take ~30-60s)...")
    research = research_topic(client, history)
    print("\n--- Research summary ---")
    print(research)

    print("\nWriting first draft...")
    draft = write_post(client, research)

    print("Critiquing and rewriting for quality...")
    post = critique_and_revise(client, draft)

    today = date.today().isoformat()
    out_dir = POSTS_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "content.json").write_text(json.dumps(post, indent=2))
    (out_dir / "draft_before_revision.json").write_text(json.dumps(draft, indent=2))

    render_all_slides(post, out_dir)

    history.append(post["tool_name"])
    save_history(history)

    print(f"\nDone. Topic: {post['tool_name']}")
    print(f"Everything saved under {out_dir.relative_to(SCRIPT_DIR)}/")
    print("(draft_before_revision.json is kept alongside content.json so you can compare)")
    print("Nothing was posted, uploaded, or pushed anywhere -- review the caption")
    print("and slides in that folder before we wire this up to Buffer.")


if __name__ == "__main__":
    main()

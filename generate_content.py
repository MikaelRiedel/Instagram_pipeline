"""
Instagram carousel content generator.

What this does:
  1. Alternates between two research modes so coverage stays ~50/50 over
     time (not left to chance, which is what drifted to "always newest"):
       - "proven": a tool with a real track record -- confirmed pricing,
         real reviews, an underrated gem or a big recent update
       - "new": a genuinely recent launch/update -- but ONLY if Claude can
         confirm real pricing and real evidence of adoption; otherwise it's
         instructed to reject that candidate and search for a different one
         rather than publish something unresearchable (this is the fix for
         posts like the 3-day-old tool with no confirmed pricing that
         slipped through before)
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
    AUDIENCE_CONTEXT,
    HISTORY_PATH,
    MODEL,
    POSTS_DIR,
    POST_SCHEMA,
    SCRIPT_DIR,
    WRITING_RULES,
    render_all_slides,
)

# Well-known incumbents to steer away from by default in BOTH modes -- the
# point of this account is surfacing things the audience hasn't already
# heard of a hundred times, not re-reviewing ChatGPT again.
OVEREXPOSED_TOOLS = [
    "ChatGPT", "Claude", "Gemini", "Microsoft Copilot", "GitHub Copilot",
    "Notion AI", "Grammarly", "Jasper", "Copy.ai", "Midjourney", "Canva Magic Studio",
]

UNCONFIRMED_RED_FLAGS = [
    "could not confirm", "cannot confirm", "not confirmed", "unable to confirm",
    "pricing not available", "pricing not yet public", "pricing tbd",
    "waitlist only", "no public pricing",
]


def load_history() -> list[str]:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


def save_history(history: list[str]) -> None:
    HISTORY_PATH.write_text(json.dumps(history, indent=2))


def sources_look_vendor_only(post: dict) -> bool:
    """Rough check that the post cites something other than the vendor's own
    site. Not exact -- it just raises a flag for the human reviewer."""
    sources = post.get("sources", [])
    if len(sources) <= 1:
        return True
    key = "".join(c for c in post.get("tool_name", "").lower() if c.isalnum())[:8]
    if not key:
        return False
    return all(key in src.lower().replace("-", "").replace(".", "") for src in sources)


def choose_mode(history: list[str]) -> str:
    """Deterministic 50/50 alternation, not left to the model's discretion --
    that's exactly what drifted to 'always pick the newest thing' before."""
    return "proven" if len(history) % 2 == 0 else "new"


def research_topic(client: anthropic.Anthropic, history: list[str], mode: str) -> str:
    avoid_list = ", ".join(history) if history else "(none yet -- this is the first post)"
    today_str = date.today().strftime("%B %d, %Y")
    overexposed = ", ".join(OVEREXPOSED_TOOLS)

    if mode == "proven":
        task = (
            "Your job: find ONE AI tool or productivity app with a genuine track record -- "
            "something that's been around long enough to have confirmed pricing and real "
            "user reviews, but that this audience likely hasn't tried yet. This can be a "
            "well-made tool that never got much attention, or an established one that just "
            "shipped a major feature worth revisiting. It does NOT need to be new -- being "
            "genuinely useful and under-the-radar matters more here than recency."
        )
    else:
        task = (
            "Your job: find ONE AI tool or productivity app that is genuinely recent -- "
            "launched, out of beta, or majorly updated within roughly the last 1-6 months.\n\n"
            "HARD requirements -- the tool you write up must have ALL THREE of these "
            "confirmed by search, not assumed:\n"
            "1. A specific, real price or pricing tier. 'Pricing not yet public', 'waitlist "
            "only', or you guessing a likely price does NOT count.\n"
            "2. Real evidence of adoption/reception -- meaningful Product Hunt comments or "
            "upvotes, press coverage, Hacker News/Reddit discussion, or review site "
            "listings. A bare directory listing with zero engagement is NOT enough.\n"
            "3. Actual recency. A tool that launched years ago does NOT qualify just "
            "because it still exists -- if you pick an established product, the angle must "
            "be a specific feature or version it shipped in the last ~6 months, and that "
            "feature must be what the post is about.\n\n"
            "Start your summary with a line in exactly this format so recency is auditable:\n"
            "RECENCY: <what launched or changed, and roughly when>\n"
            "If you can't fill that line in honestly with something from the last ~6 "
            "months, this candidate does not qualify for today."
        )

    # Search budget matters a lot: every result stays in context and gets
    # re-billed on each later turn of the tool loop, so search count drives
    # cost super-linearly. Hence the explicit plan + hard cap below.
    search_discipline = (
        "\n\nSEARCH BUDGET -- important: you have a hard limit of 4 web searches, and each "
        "one materially increases cost. Use them deliberately:\n"
        "- 1 broad discovery search to surface candidates\n"
        "- 2-3 targeted searches to verify the single most promising candidate (its pricing "
        "page, and reviews/discussion of it)\n"
        "Do not browse speculatively or explore candidates you've already ruled out.\n\n"
        "If you run out of searches without being able to confirm the requirements above, "
        "do NOT write up an unverified tool and do NOT pad the summary with caveats. "
        "Instead, reply with exactly 'NO QUALIFYING CANDIDATE' on the first line, followed "
        "by a one-line reason. A skipped day is much better than a post with made-up "
        "pricing."
    )
    task += search_discipline

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}],
        messages=[{
            "role": "user",
            "content": (
                f"Today's date is {today_str}. You're researching content for a daily "
                f"Instagram account that reviews AI tools and productivity software. "
                f"{AUDIENCE_CONTEXT} Monetized via affiliate links, so credibility -- and "
                "picking tools that could realistically have an affiliate/referral program "
                "-- matters more than hype.\n\n"
                f"{task}\n\n"
                f"Do NOT pick: {overexposed} (too oversaturated to be interesting) -- unless "
                "you find a genuinely newsworthy angle, like a brand-new flagship feature or "
                "version launch, in which case name that specific angle explicitly.\n"
                f"Do NOT repeat any of these already-covered topics: {avoid_list}.\n\n"
                "Once you have a qualifying candidate, confirm via web search: its current "
                "pricing tiers, its 3-5 most useful features, and at least one honest "
                "limitation or downside -- don't only write positives, credibility matters "
                "more than hype.\n\n"
                "Reply with a plain-text research summary covering: tool name, why it's "
                "worth covering, pricing, features, the limitation, and the source URLs "
                "you used."
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

    mode = choose_mode(history)
    print(f"Mode for today: {mode} ({'proven track record' if mode == 'proven' else 'genuinely new, must be verifiable'})")

    print("Researching a topic (this calls the web -- may take ~30-60s)...")
    research = research_topic(client, history, mode)
    print("\n--- Research summary ---")
    print(research)

    if research.strip().upper().startswith("NO QUALIFYING CANDIDATE"):
        print(
            "\nResearch found nothing that met the bar (confirmed pricing + real adoption "
            "evidence), so no post was generated today.\n"
            "That's the intended behaviour -- a skipped day beats a post with invented "
            "pricing. Nothing was written, rendered, or charged for writing."
        )
        sys.exit(0)

    lowered = research.lower()
    if any(flag in lowered for flag in UNCONFIRMED_RED_FLAGS):
        print(
            "\n/!\\ WARNING: this research mentions unconfirmed pricing/details despite "
            "being told not to -- double check the caption's claims carefully before "
            "approving this post."
        )

    print("\nWriting first draft...")
    draft = write_post(client, research)

    print("Critiquing and rewriting for quality...")
    post = critique_and_revise(client, draft)

    if sources_look_vendor_only(post):
        print(
            "\n/!\\ WARNING: the only sources cited look like the vendor's own site. "
            "Nothing independently confirms real users rate this tool -- worth a manual "
            "check before approving."
        )

    today = date.today().isoformat()
    out_dir = POSTS_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "content.json").write_text(json.dumps(post, indent=2))
    (out_dir / "draft_before_revision.json").write_text(json.dumps(draft, indent=2))

    render_all_slides(post, out_dir)

    history.append(post["tool_name"])
    save_history(history)

    print(f"\nDone. Topic: {post['tool_name']} (mode: {mode})")
    print(f"Everything saved under {out_dir.relative_to(SCRIPT_DIR)}/")
    print("(draft_before_revision.json is kept alongside content.json so you can compare)")
    print("Nothing was posted, uploaded, or pushed anywhere -- review the caption")
    print("and slides in that folder before we wire this up to Buffer.")


if __name__ == "__main__":
    main()

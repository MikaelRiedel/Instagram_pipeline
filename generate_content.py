"""
Instagram carousel content generator.

What this does:
  1. Alternates between two research modes so coverage stays ~50/50 over
     time (not left to chance, which is what drifted to "always newest"):
       - "proven": a tool with a real track record -- real users and
         discussion, an underrated gem or a big recent update
       - "new": a genuinely recent launch/update -- but only if it's real and
         usable today, not vaporware or a waitlist (the fix for the 3-day-old
         tool with nothing verifiable that slipped through before)
     Research aims at what makes a tool exciting -- its standout capability
     and what drudgery it removes -- not at pricing tiers, which made earlier
     posts read like spec sheets.
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
    _structured_json,
    playbook_prompt_block,
    strip_false_affiliate_claim,
    render_all_slides,
)

# Well-known, high-search-volume tools. A zero-follower account has no
# audience graph, so Search and hashtags are the only discovery surface --
# and both only surface posts against existing query/tag volume. Avoiding
# these entirely (as this list used to do) meant every post targeted a tag
# with ~zero traffic, which is why reach has been stuck near 0. Now used to
# require a differentiated ANGLE on these tools instead of avoiding them.
HEAD_TERM_TOOLS = [
    "ChatGPT", "Claude", "Gemini", "Microsoft Copilot", "GitHub Copilot",
    "Notion AI", "Grammarly", "Jasper", "Copy.ai", "Midjourney", "Canva Magic Studio",
]

# Pricing-related flags deliberately removed -- pricing is no longer something
# the post needs, so "no public pricing" is not a problem. What still matters
# is whether the tool is actually real and usable.
UNCONFIRMED_RED_FLAGS = [
    "could not confirm", "cannot confirm", "not confirmed", "unable to confirm",
    "waitlist only", "not yet launched", "coming soon", "invite only",
]


def load_history() -> list[str]:
    if HISTORY_PATH.exists():
        return json.loads(HISTORY_PATH.read_text())
    return []


def save_history(history: list[str]) -> None:
    HISTORY_PATH.write_text(json.dumps(history, indent=2))


def sources_look_vendor_only(post: dict) -> bool:
    """Rough check that the post cites something other than the vendor's own
    site. Compares domains only -- review sites routinely put the tool's name
    in the URL path (g2.com/products/krisp), which used to trip a false alarm."""
    from urllib.parse import urlparse

    def domain(url: str) -> str:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
        return host[4:] if host.startswith("www.") else host

    vendor = domain(post.get("tool_url", ""))
    others = {domain(s) for s in post.get("sources", [])} - {vendor, ""}
    return not others


def choose_mode(history: list[str]) -> str:
    """Deterministic 50/50 alternation, not left to the model's discretion --
    that's exactly what drifted to 'always pick the newest thing' before."""
    return "proven" if len(history) % 2 == 0 else "new"


def research_topic(client: anthropic.Anthropic, history: list[str], mode: str) -> str:
    avoid_list = ", ".join(history) if history else "(none yet -- this is the first post)"
    today_str = date.today().strftime("%B %d, %Y")
    head_terms = ", ".join(HEAD_TERM_TOOLS)

    if mode == "proven":
        task = (
            "Your job: find ONE AI tool or productivity app with a genuine track record -- "
            "something that's been around long enough that real people use and discuss it, "
            "but that this audience likely hasn't tried yet. A well-made tool that never got "
            "much attention, or an established one that just shipped something worth "
            "revisiting. It does NOT need to be new -- being genuinely useful and "
            "under-the-radar matters more here than recency."
        )
    else:
        task = (
            "Your job: find ONE AI tool or productivity app that is genuinely recent -- "
            "launched, out of beta, or majorly updated within roughly the last 1-6 months. "
            "If you pick an established product, the angle must be something it shipped in "
            "that window, and that new thing must be what the post is about.\n\n"
            "Start your summary with this line so recency is auditable:\n"
            "RECENCY: <what launched or changed, and roughly when>"
        )

    # Only two bars now, and neither is pricing -- pricing ate the search
    # budget and turned posts into spec sheets. What actually matters is that
    # the tool is real and that there's something exciting to say about it.
    task += (
        "\n\nTwo things must be true of whatever you pick:\n"
        "1. It is REAL and usable today -- not a waitlist, not a concept, not vaporware. "
        "Someone reading this post must be able to go and try it.\n"
        "2. There is some real-world signal it exists and works -- press, a review, a "
        "Product Hunt or Reddit or Hacker News discussion, anything beyond the vendor's "
        "own marketing page.\n\n"
        "WHAT TO ACTUALLY RESEARCH -- this is for an inspiring post, not a spec sheet. "
        "Spend your effort finding:\n"
        "- The single most impressive or surprising thing it does (the 'wait, that's "
        "possible?' capability)\n"
        "- A vivid, concrete picture of what using it replaces -- the tedious thing it "
        "removes from someone's day\n"
        "- Exact feature names and real capabilities, so the post can be specific rather "
        "than vague\n"
        "- One honest limitation\n"
        "Do NOT spend searches hunting down pricing tiers, quotas, or usage limits. Price "
        "is not what makes someone want a tool, and the post will barely mention it."
    )

    # Search results accumulate in context and get re-billed on every later
    # turn of the loop, so search count drives cost super-linearly.
    task += (
        "\n\nSEARCH BUDGET: you have 5 web searches, and each one materially increases "
        "cost. Plan them: 1 broad discovery search, then targeted ones on the single most "
        "promising candidate. Don't re-explore candidates you've ruled out.\n\n"
        "If you run low on searches, work with what you have -- as long as the tool is "
        "clearly real and usable, write the post. Only reply with 'NO QUALIFYING "
        "CANDIDATE' on the first line (plus a one-line reason) if you genuinely could not "
        "find any tool that's real and usable -- that bar is about avoiding vaporware, not "
        "about having every detail nailed down."
    )

    # Streamed, not a plain create(): this call runs a multi-step web-search
    # loop that can exceed the SDK's 10-minute request timeout. A timeout here
    # silently triggers retries, each re-running every search from scratch --
    # which is how a "10 minute" run turned into 30 minutes and 3x the cost.
    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
        messages=[{
            "role": "user",
            "content": (
                f"Today's date is {today_str}. You're researching content for a daily "
                f"Instagram account that reviews AI tools and productivity software. "
                f"{AUDIENCE_CONTEXT} Monetized via affiliate links, so credibility -- and "
                "picking tools that could realistically have an affiliate/referral program "
                "-- matters more than hype.\n\n"
                f"{task}\n\n"
                f"These are high-search-volume, head-term tools: {head_terms}. Picking one of "
                "them is fine, and often better for discovery than a tool nobody is searching "
                "for -- but only if the post's angle is a specific underused feature or a "
                "direct comparison (e.g. 'the Notion AI feature nobody uses', 'Otter vs "
                "Grammarly for meeting notes'), not a generic overview of the tool. The angle "
                "must be the differentiator here, not the tool's obscurity.\n"
                f"Do NOT repeat any of these already-covered topics: {avoid_list}.\n\n"
                "Reply with a plain-text research summary. Start with this line exactly, "
                "since it's used to fetch the product's own screenshot for the slides:\n"
                "OFFICIAL_URL: <the tool's real homepage, e.g. https://example.com>\n\n"
                "Then cover: the tool's name, the single most impressive thing it does, "
                "what tedious task it replaces, its standout features by name, one honest "
                "limitation, and the source URLs you used. Write the summary so that "
                "someone reading only it could produce an exciting post -- lead with what "
                "makes this genuinely interesting, not with a feature inventory."
            ),
        }],
    ) as stream:
        response = stream.get_final_message()

    text = "".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        # Seen on 2026-09-16: the turn ended on search results with no prose,
        # most likely the server-tool budget running out mid-loop. Report the
        # reason rather than returning "" and letting the caller pay for a
        # write call it can't possibly fulfil.
        searches = sum(1 for b in response.content if b.type == "server_tool_use")
        raise RuntimeError(
            f"Research produced no text (stop_reason={response.stop_reason}, "
            f"{searches} searches used). Nothing to write a post from."
        )
    return text



def write_post(client: anthropic.Anthropic, research: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                "Using this research, write an Instagram carousel post that makes people "
                "want to go try this tool:\n\n"
                f"{research}\n\n{WRITING_RULES}{playbook_prompt_block()}"
            ),
        }],
    )
    return _structured_json(response)


def critique_and_revise(client: anthropic.Anthropic, draft: dict) -> dict:
    """Self-critique pass: a first draft from an LLM is reliably generic.
    This forces Claude to find its own clichés/vagueness and fix them."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                "Here is a draft Instagram post:\n\n"
                f"{json.dumps(draft, indent=2)}\n\n"
                "Critique it harshly against ONE test: would someone scrolling past this "
                "stop, read it, and want the tool by the end? Specifically flag:\n"
                "- A hook that doesn't earn the swipe\n"
                "- Any slide that reads like a spec sheet, feature label, or pricing table "
                "instead of something the reader would WANT\n"
                "- Generic marketing adjectives ('powerful', 'game-changing', 'seamless')\n"
                "- Vagueness where a vivid, specific picture was available in the facts\n"
                "- Anything that sounds like a product brochure rather than a person who "
                "found something genuinely cool\n"
                "Then rewrite the whole post fixing every issue -- same tool, same facts, "
                "but substantially more desirable to read. "
                f"{WRITING_RULES}{playbook_prompt_block()}\n"
                "Return ONLY the improved, rewritten version in the schema -- not the "
                "critique itself."
            ),
        }],
    )
    return _structured_json(response)


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print('Set ANTHROPIC_API_KEY first, e.g.:\n  export ANTHROPIC_API_KEY="your-key-here"')
        sys.exit(1)

    # max_retries=0 is deliberate: a retry of the research call re-runs every
    # web search and gets billed again. A failed run that costs once and exits
    # is far better than a silent 3x charge for output nobody ever receives.
    client = anthropic.Anthropic(max_retries=0, timeout=600.0)
    history = load_history()

    mode = choose_mode(history)
    print(f"Mode for today: {mode} ({'proven track record' if mode == 'proven' else 'genuinely new, must be verifiable'})")

    print("Researching a topic (this calls the web -- may take ~30-60s)...")
    research = research_topic(client, history, mode)
    print("\n--- Research summary ---")
    print(research)

    if research.strip().upper().startswith("NO QUALIFYING CANDIDATE"):
        print(
            "\nResearch couldn't find a tool that's actually real and usable today, so no "
            "post was generated.\n"
            "That's the intended behaviour -- a skipped day beats posting about vaporware. "
            "Nothing was written, rendered, or charged for writing."
        )
        sys.exit(0)

    lowered = research.lower()
    if any(flag in lowered for flag in UNCONFIRMED_RED_FLAGS):
        print(
            "\n/!\\ WARNING: this research suggests the tool may not be fully launched or "
            "usable yet (waitlist, invite-only, unconfirmed details) -- worth a manual "
            "check before this goes out."
        )

    print("\nWriting first draft...")
    draft = write_post(client, research)

    print("Critiquing and rewriting for quality...")
    post = critique_and_revise(client, draft)

    if strip_false_affiliate_claim(post):
        print("\n/!\\ Removed an invented affiliate disclosure from the caption -- "
              "no affiliate programs are joined, so the claim would have been false.")

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

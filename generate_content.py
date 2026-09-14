"""
Instagram carousel content generator.

What this does:
  1. Asks Claude to hunt for a recently launched/updated AI tool (using live
     web search), actively avoiding stale, overexposed picks and anything
     already covered (see history.json)
  2. Asks Claude to write a caption + carousel slides, then critiques and
     rewrites its own draft for specificity and punch (a generic first draft
     is expected -- the revision pass is what fixes it)
  3. Renders each slide as a designed image with Pillow (accurate text
     wrapping, vertical centering, alternating hook/body/CTA styling,
     progress dots)
  4. Saves everything under posts/<date>/ -- nothing is uploaded, posted,
     or pushed to GitHub by this script. That's a later, separate step.

Setup (once):
    pip install anthropic pillow
    export ANTHROPIC_API_KEY="your-key-here"

Optional but recommended: put a *-Bold.ttf and a *-Regular.ttf font file
(e.g. Inter, free from https://fonts.google.com/specimen/Inter) into
assets/fonts/ next to this script. Without them, slides render in a plain
fallback font.

Run:
    python3 generate_content.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import anthropic
from PIL import Image, ImageDraw, ImageFont

MODEL = "claude-sonnet-5"

SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_PATH = SCRIPT_DIR / "history.json"
POSTS_DIR = SCRIPT_DIR / "posts"
FONT_DIR = SCRIPT_DIR / "assets" / "fonts"

# --- Brand/appearance settings -- tweak these freely ---
SLIDE_SIZE = (1080, 1350)  # Instagram portrait 4:5
DARK_BG = "#15151F"
DARK_HEADING = "#F7F7FB"
DARK_BODY = "#B8B8C8"
ACCENT_COLOR = "#7B61FF"      # used as the "pop" color on dark slides
ACCENT_BG = "#7B61FF"         # background for hook/CTA slides
ACCENT_TEXT = "#15151F"       # text color on the accent background
ACCENT_BODY = "#2B2350"
MARGIN = 96

# Well-known incumbents to steer away from by default -- the point of this
# account is surfacing new/interesting tools, not re-reviewing ChatGPT for
# the hundredth time. Claude can still override this if there's a genuinely
# newsworthy angle (a major new feature/version), per the prompt below.
OVEREXPOSED_TOOLS = [
    "ChatGPT", "Claude", "Gemini", "Microsoft Copilot", "GitHub Copilot",
    "Notion AI", "Grammarly", "Jasper", "Copy.ai", "Midjourney", "Canva Magic Studio",
]

BANNED_PHRASES = [
    "game-changer", "game changing", "revolutionize", "revolutionary",
    "unlock the power of", "take it to the next level", "elevate your",
    "seamless", "seamlessly", "look no further", "in today's fast-paced world",
    "supercharge", "unleash", "dive in", "game changer", "cutting-edge",
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


POST_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_name": {"type": "string"},
        "caption": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["heading", "body"],
                "additionalProperties": False,
            },
        },
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tool_name", "caption", "slides", "hashtags", "sources"],
    "additionalProperties": False,
}

WRITING_RULES = (
    "Requirements:\n"
    "- Tone: like a sharp, specific friend telling you about something they actually "
    "tried -- not an ad. Include the limitation, don't oversell.\n"
    "- Be CONCRETE, not generic: use real numbers, exact feature names, exact pricing, "
    "and a specific example of what someone would actually use this for. Never settle for "
    "vague claims like 'powerful' or 'boosts productivity' when a specific detail from the "
    "research is available instead.\n"
    f"- Banned words/phrases -- do not use any of these anywhere: {', '.join(BANNED_PHRASES)}.\n"
    "- caption: an Instagram caption (under 2200 characters). The FIRST LINE must be a "
    "scroll-stopping hook -- a specific claim, number, or contrarian observation, not a "
    "generic opener like 'Have you heard of...' or 'Let's talk about...'. It MUST clearly "
    "disclose the affiliate relationship (e.g. 'This post contains affiliate links') and "
    "end by pointing to the bio link (e.g. 'Full breakdown -- link in bio'). Do NOT include "
    "any raw URLs in the caption -- Instagram won't make them clickable anyway.\n"
    "- slides: produce between 5 and 8 carousel slides. Slide 1 is the hook -- it should "
    "work as a standalone thumbnail, punchy, under 8 words if possible. Middle slides each "
    "cover ONE specific, concrete point (a feature, a price, a real use case) -- no filler "
    "slides that just restate the hook. The last slide is the CTA, pointing to the bio "
    "link. Each slide needs a short heading (under 40 characters) and a 1-2 sentence body "
    "written to fit on a graphic, not a paragraph.\n"
    "- hashtags: 5-10 relevant hashtags, no '#' symbol included.\n"
    "- sources: the URLs you used for pricing/feature facts, so a human can spot-check "
    "them.\n"
)


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


def _find_font_file(bold: bool) -> Path | None:
    # Matches both a plain "Inter-Bold.ttf" and Google Fonts' actual naming,
    # e.g. "Inter_18pt-Bold.ttf" / "Inter_24pt-Bold.ttf" -- the suffix match
    # already excludes "*-BoldItalic.ttf" variants.
    suffix = "-Bold.ttf" if bold else "-Regular.ttf"
    if not FONT_DIR.exists():
        return None
    matches = sorted(FONT_DIR.glob(f"*{suffix}"))
    return matches[0] if matches else None


_FONT_WARNED: set[bool] = set()


def _load_font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    path = _find_font_file(bold)
    if path is not None:
        return ImageFont.truetype(str(path), size)
    if bold not in _FONT_WARNED:
        weight = "-Bold" if bold else "-Regular"
        print(f"Note: no *{weight}.ttf found in {FONT_DIR}, falling back to a plain default font.")
        _FONT_WARNED.add(bold)
    return ImageFont.load_default(size=size)


def _wrap_by_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word-wrap measured in actual rendered pixel width, not an
    average-character-width guess -- avoids uneven/overflowing lines,
    especially with bold headings where character widths vary a lot."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _text_block_height(lines: list[str], font: ImageFont.FreeTypeFont, line_spacing: float) -> int:
    line_height = int(font.size * line_spacing)
    return line_height * len(lines)


def _draw_block(draw, lines: list[str], font, x: int, y: int, fill, line_spacing=1.25) -> int:
    line_height = int(font.size * line_spacing)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _draw_progress_dots(draw, index: int, total: int, width: int, height: int, dot_color, active_color) -> None:
    radius = 7
    gap = 26
    total_width = total * (2 * radius) + (total - 1) * gap
    start_x = (width - total_width) // 2
    cy = height - MARGIN // 2 - radius
    for i in range(total):
        cx = start_x + i * (2 * radius + gap) + radius
        color = active_color if i == index - 1 else dot_color
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)


def render_slide(index: int, total: int, heading: str, body: str, out_path: Path) -> None:
    width, height = SLIDE_SIZE
    is_hook = index == 1
    is_cta = index == total
    accent_slide = is_hook or is_cta

    bg = ACCENT_BG if accent_slide else DARK_BG
    heading_color = ACCENT_TEXT if accent_slide else DARK_HEADING
    body_color = ACCENT_BODY if accent_slide else DARK_BODY
    dot_color = "#3A3A55" if not accent_slide else "#9C8CFF"
    active_dot = ACCENT_COLOR if not accent_slide else ACCENT_TEXT

    img = Image.new("RGB", SLIDE_SIZE, bg)
    draw = ImageDraw.Draw(img)

    heading_size = 84 if is_hook else 60
    heading_font = _load_font(bold=True, size=heading_size)
    body_font = _load_font(bold=False, size=42)

    content_width = width - 2 * MARGIN
    heading_lines = _wrap_by_pixels(draw, heading, heading_font, content_width)
    body_lines = _wrap_by_pixels(draw, body, body_font, content_width)

    gap_between = 44
    block_height = (
        _text_block_height(heading_lines, heading_font, 1.15)
        + gap_between
        + _text_block_height(body_lines, body_font, 1.3)
    )

    # Vertically center the text block, leaving room for the dot row at the bottom.
    usable_height = height - MARGIN - 120  # 120 reserves space for dots + breathing room
    y = MARGIN + max(0, (usable_height - block_height) // 2)

    # Small accent bar above the heading as a consistent brand mark.
    bar_color = ACCENT_TEXT if accent_slide else ACCENT_COLOR
    draw.rectangle((MARGIN, y, MARGIN + 64, y + 6), fill=bar_color)
    y += 30

    y = _draw_block(draw, heading_lines, heading_font, MARGIN, y, heading_color, line_spacing=1.15)
    y += gap_between
    _draw_block(draw, body_lines, body_font, MARGIN, y, body_color, line_spacing=1.3)

    _draw_progress_dots(draw, index, total, width, height, dot_color, active_dot)

    img.save(out_path)


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

    slides = post["slides"]
    for i, slide in enumerate(slides, start=1):
        out_path = out_dir / f"slide_{i:02d}.png"
        render_slide(i, len(slides), slide["heading"], slide["body"], out_path)
        print(f"  wrote {out_path.relative_to(SCRIPT_DIR)}")

    history.append(post["tool_name"])
    save_history(history)

    print(f"\nDone. Topic: {post['tool_name']}")
    print(f"Everything saved under {out_dir.relative_to(SCRIPT_DIR)}/")
    print("(draft_before_revision.json is kept alongside content.json so you can compare)")
    print("Nothing was posted, uploaded, or pushed anywhere -- review the caption")
    print("and slides in that folder before we wire this up to Buffer.")


if __name__ == "__main__":
    main()

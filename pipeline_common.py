"""
Shared code for the Instagram content pipeline scripts (generate_content.py,
publish_to_buffer.py, revise_content.py) -- git/GitHub helpers, the Buffer
GraphQL client, the post schema + writing rules, and slide image rendering.

Not meant to be run directly.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

import imagery

# --- Shared locations ---
SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_PATH = SCRIPT_DIR / "history.json"
POSTS_DIR = SCRIPT_DIR / "posts"
PROJECT_DIR = SCRIPT_DIR / "project"
PLAYBOOK_PATH = PROJECT_DIR / "playbook.md"
POSTS_LOG_PATH = PROJECT_DIR / "posts.jsonl"
FONT_DIR = SCRIPT_DIR / "assets" / "fonts"

MODEL = "claude-sonnet-5"
API_URL = "https://api.buffer.com"


# ============================================================
# Post schema + writing rules (used by generate_content.py and
# revise_content.py so revisions follow the same standards as first drafts)
# ============================================================

POST_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_name": {"type": "string"},
        "tool_url": {"type": "string"},
        "caption": {"type": "string"},
        # Which hook shape slide 1 uses. Recorded per post so the stats loop
        # can tell which shapes actually earn saves.
        "hook_type": {
            "type": "string",
            "enum": ["direct_benefit", "named_pain", "contrarian", "curiosity_gap", "specific_number"],
        },
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                    # Drives what artwork the slide gets: a real screenshot of
                    # the tool, a mood photo, or a pure graphic treatment.
                    "image_kind": {"type": "string", "enum": ["product", "photo", "graphic"]},
                    # Stock photo search terms, only used when kind == photo.
                    "image_query": {"type": "string"},
                },
                "required": ["heading", "body", "image_kind", "image_query"],
                "additionalProperties": False,
            },
        },
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["tool_name", "tool_url", "caption", "hook_type", "slides", "hashtags", "sources"],
    "additionalProperties": False,
}

BANNED_PHRASES = [
    "game-changer", "game changing", "revolutionize", "revolutionary",
    "unlock the power of", "take it to the next level", "elevate your",
    "seamless", "seamlessly", "look no further", "in today's fast-paced world",
    "supercharge", "unleash", "dive in", "game changer", "cutting-edge",
]

# True  = posts go straight into Buffer's queue and publish on their own.
# False = posts land as drafts in Buffer for manual review before sending.
AUTO_PUBLISH = True

# Flip to True ONLY once real affiliate links actually exist in the bio link
# destination. Until then, claiming "this post contains affiliate links" is a
# false disclosure -- the mirror image of faking personal experience.
HAS_AFFILIATE_LINKS = False

_DISCLOSURE_RULE = (
    "It MUST clearly disclose the affiliate relationship (e.g. 'This post contains "
    "affiliate links') and end by pointing to the bio link (e.g. 'Full breakdown -- link "
    "in bio')."
    if HAS_AFFILIATE_LINKS
    else
    "End by pointing to the bio link (e.g. 'Full breakdown -- link in bio'). Do NOT claim "
    "the post contains affiliate links or any paid relationship -- there aren't any yet, "
    "and saying otherwise would be a false disclosure."
)

AUDIENCE_CONTEXT = (
    "Audience: general productivity and AI enthusiasts -- curious people who enjoy "
    "discovering new tools and workflows for both work and personal life. NOT specifically "
    "founders/businesses chasing ROI -- frame value as 'this makes your work or day-to-day "
    "life easier, faster, or more interesting to explore', not business-case language."
)

WRITING_RULES = (
    f"{AUDIENCE_CONTEXT}\n\n"
    "Requirements:\n"
    "- CRITICAL -- never fabricate personal experience. You have not used this tool. Do "
    "NOT write things like 'I've been using this for 3 months', 'the feature I use most', "
    "or 'I spend a few minutes a week fixing it'. Invented first-person anecdotes on an "
    "affiliate post are a real compliance problem (FTC endorsement rules require stated "
    "experience to be genuine), not just a style issue. Write as a well-informed reviewer "
    "describing what the tool does and what its users report -- 'It joins your Zoom call "
    "and...', 'Users consistently flag that...'. Specificity must come from the research, "
    "never from a made-up anecdote.\n"
    "- THE JOB OF THIS POST is to make the reader want the tool -- to feel 'I need to try "
    "this'. Sell the TRANSFORMATION, not the spec sheet. Lead with what becomes possible, "
    "what disappears from their day, or the thing they didn't know software could do yet. "
    "A reader should finish the carousel thinking about their own life, not about feature "
    "checkboxes.\n"
    "- PRICING IS NOT THE STORY. Do not build slides around price, tiers, quotas, or usage "
    "limits -- nobody gets excited about a pricing table. At most, mention cost once in "
    "passing in the caption if it's genuinely remarkable (e.g. it's free, or startlingly "
    "cheap for what it does). Never give pricing its own slide.\n"
    "- But inspiring does NOT mean vague. This is the hard part: be evocative AND concrete "
    "at the same time. The desire comes from a specific, vivid picture -- 'it sits in your "
    "meetings and hands you the decisions afterwards' beats both 'boosts productivity' "
    "(vague) and '1,200 transcription minutes per month' (a spec). Use exact feature names "
    "and real capabilities from the research, framed as what they DO for the reader.\n"
    "- OPTIMISE FOR SAVES AND SENDS, not likes. Saves are the strongest ranking signal and "
    "shares to a friend are what reach new people. Practical test for every slide: would "
    "someone screenshot this, or send it to a friend who'd find it useful? If a slide "
    "wouldn't survive that test, it's filler -- cut or rewrite it.\n"
    "- One idea per slide. If a sentence contains 'and', check whether it's really two "
    "ideas that deserve two slides.\n"
    "- No unexplained jargon. Terms like LLM, API, RAG, fine-tuning, prompt engineering "
    "either get cut or get a plain-language clause right there.\n"
    f"- Banned words/phrases -- do not use any of these anywhere: {', '.join(BANNED_PHRASES)}.\n"
    "- caption: an Instagram caption (under 2200 characters). The FIRST LINE is its own "
    "hook -- it's all that shows before 'more', so it must do the same work slide 1 does. "
    "The caption can go deeper than the slides: the backstory, the why, the context that "
    "wouldn't fit on a graphic. Finish with the same call to action as the final slide, "
    "reinforced -- e.g. 'Save this so you don't lose it'. "
    f"{_DISCLOSURE_RULE} Do NOT include "
    "any raw URLs in the caption -- Instagram won't make them clickable anyway.\n"
    "- slides: produce 8 to 10 slides. Don't pad to 10 if the idea runs out at 8, and "
    "don't cram 12 slides of content into 8. The arc:\n"
    "    1. HOOK -- works alone as a thumbnail, and carries ~80% of whether anyone swipes, "
    "so spend the most effort here. Max 12 words. Use one of these shapes: direct benefit "
    "with a no-jargon promise ('Write a month of captions in 10 minutes -- no prompt "
    "writing needed'); a plainly named pain ('You don't need to learn AI, you need 3 tools "
    "that do it for you'); permission-giving or contrarian ('You don't need ChatGPT Plus "
    "for this'); a curiosity gap ('The AI tool quietly doing your job in the background'); "
    "or a specific number ('5 AI tools that save an hour a day'). Never vague ('AI is "
    "changing everything'), never jargon, and it must make sense without slide 2.\n"
    "    2. THE SETUP -- why this matters right now, in one sentence. The tedious or slow "
    "thing the reader recognises from their own week.\n"
    "    3-7. THE WALKTHROUGH -- one vivid capability or step per slide, written as an "
    "outcome ('ask it what you decided three weeks ago and it just answers'), never as a "
    "feature label. At least one should land an 'I didn't know that was possible' moment. "
    "Fold the one honest limitation into a slide here, stated plainly in a sentence -- "
    "credibility is what makes the rest believable.\n"
    "    8. THE PAYOFF -- the concrete result. A before/after, or 'that whole thing now "
    "takes four minutes'.\n"
    "    9 (optional). RECAP -- one line summarising the whole carousel, for people who "
    "skim to the end.\n"
    "    Last. CTA -- tell them exactly what to do: save it, try the tool this week, "
    "follow for more. Never end without one.\n"
    "  Word limits, because text that overflows gets shrunk until it's unreadable: heading "
    "max ~12 words, body max ~30 words. If it won't fit, that means it needs its own "
    "slide, not smaller type. No slide may be a pricing table.\n"
    "- image_kind on each slide -- this decides the artwork:\n"
    "    'product' = shows a real screenshot of the tool. Use for 2-3 of the walkthrough "
    "slides, where seeing the actual software helps.\n"
    "    'photo' = a real photograph setting the mood. Use for the hook, the setup, and "
    "the payoff -- slides that are about the reader's life, not the software.\n"
    "    'graphic' = type only. Use for the recap and CTA, and anywhere a picture would "
    "just be decoration.\n"
    "- image_query: only matters when image_kind is 'photo'. Give 2-4 plain words "
    "describing the photograph you want ('tired person laptop night', 'sunlit desk "
    "morning coffee'). Describe a real photographable scene, not an abstract concept. "
    "Leave it as an empty string for the other kinds.\n"
    "- tool_url: the tool's official homepage, used to fetch its product screenshot. Get "
    "this right -- a wrong URL means no product imagery.\n"
    "- hook_type: which shape slide 1 uses -- direct_benefit, named_pain, contrarian, "
    "curiosity_gap, or specific_number. Report honestly what you actually wrote; this is "
    "measured against results later.\n"
    "- hashtags: 5-10 relevant hashtags, no '#' symbol included.\n"
    "- sources: the URLs you used for factual claims, so a human can spot-check them. At "
    "least one should be independent of the vendor (a review, a press piece, a "
    "Reddit/Hacker News/Product Hunt discussion) where one exists.\n"
)

def _structured_json(response) -> dict:
    """Pulls the JSON out of a structured-output response.

    Joins EVERY text block: a long response gets split across several, and
    taking just the first one hands json.loads a string that stops mid-value.
    That's exactly what broke the 2026-09-16 run.
    """
    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            "Model hit max_tokens before closing the JSON -- raise max_tokens "
            "or ask for fewer slides."
        )
    text = "".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        raise RuntimeError(f"Model returned no text at all (stop_reason={response.stop_reason}).")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Model output wasn't valid JSON: {exc}\n"
            f"Got {len(text)} chars, starting: {text[:200]!r}"
        ) from exc



def load_playbook_directives() -> list[str]:
    """Reads project/playbook.md and returns the directives as plain strings.

    These are INSTRUCTIONS for generation, not background reading -- the whole
    self-improving loop hinges on them reaching the prompt. A directive is a
    top-level "- " bullet; its indented *Reason:* line is context for humans
    and is left out.
    """
    if not PLAYBOOK_PATH.exists():
        return []
    directives, current = [], None
    for raw in PLAYBOOK_PATH.read_text().splitlines():
        if raw.startswith("- "):
            if current:
                directives.append(" ".join(current.split()))
            current = raw[2:]
        elif current is not None and raw.startswith("  ") and not raw.strip().startswith("*"):
            current += " " + raw.strip()
        elif current and (not raw.strip() or raw.strip().startswith("*")):
            directives.append(" ".join(current.split()))
            current = None
    if current:
        directives.append(" ".join(current.split()))
    return [d for d in directives if d]


def playbook_prompt_block() -> str:
    """The playbook rendered for inclusion in a generation prompt."""
    directives = load_playbook_directives()
    if not directives:
        return ""
    lines = "\n".join(f"- {d}" for d in directives)
    return (
        "\n\nLEARNED DIRECTIVES -- these come from measured results and review of "
        "this account's own posts. They override general instincts and any "
        "generic best practice you might otherwise apply. Follow every one:\n"
        f"{lines}\n"
    )


def record_published_post(post_id: str, post_dir_name: str, content: dict) -> None:
    """Appends one line to project/posts.jsonl linking a published post to the
    directives that were in force when it was generated.

    Without this link the stats loop can see that a post did well but not why,
    and can never retire a directive that isn't pulling its weight.
    """
    import datetime as _dt

    entry = {
        "post_id": post_id,
        "date": post_dir_name,
        "recorded_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "tool_name": content.get("tool_name"),
        "format": "carousel",
        "hook_type": content.get("hook_type"),
        "slide_count": len(content.get("slides", [])),
        "image_kinds": [s.get("image_kind") for s in content.get("slides", [])],
        "directives_in_force": load_playbook_directives(),
    }
    POSTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with POSTS_LOG_PATH.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def strip_false_affiliate_claim(content: dict) -> bool:
    """Removes any affiliate disclosure from the caption when no affiliate
    relationship exists. Returns True if something was stripped.

    The prompt already forbids this, but a prompt is not a guarantee: the
    2026-09-14 post invented "This post contains affiliate links" anyway,
    because that sentence is everywhere in the training data for captions of
    this shape. A false material-connection claim is a compliance problem in
    its own right, and once real programs are joined this same code has to get
    the distinction right every single day. Cheap deterministic check.
    """
    if HAS_AFFILIATE_LINKS:
        return False
    caption = content.get("caption", "")
    kept = [ln for ln in caption.split("\n") if "affiliate" not in ln.lower()]
    if len(kept) == len(caption.split("\n")):
        return False
    content["caption"] = "\n".join(kept).strip()
    return True


def build_caption(content: dict) -> str:
    hashtags = " ".join(f"#{tag}" for tag in content.get("hashtags", []))
    if hashtags:
        return f"{content['caption']}\n\n{hashtags}"
    return content["caption"]


# ============================================================
# Git / GitHub helpers
# ============================================================

def run_git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=SCRIPT_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


def get_repo_slug() -> tuple[str, str]:
    """Returns (owner, repo) parsed from the 'origin' remote URL."""
    url = run_git("remote", "get-url", "origin")
    match = re.search(r"github\.com[:/](.+?)/(.+?)(\.git)?$", url)
    if not match:
        raise RuntimeError(f"Couldn't parse a GitHub owner/repo from remote url: {url}")
    return match.group(1), match.group(2)


def commit_and_push(paths: list[Path], message: str) -> str:
    """Commits any of the given paths that exist and have changes, pushes,
    and returns the current branch name."""
    # symbolic-ref (unlike rev-parse --abbrev-ref) works even before the
    # repo's first commit exists, which matters for a freshly created
    # GitHub repo cloned with no README/initial commit.
    branch = run_git("symbolic-ref", "--short", "HEAD")
    existing = [str(p) for p in paths if p.exists()]
    for p in existing:
        run_git("add", p)
    status = run_git("status", "--porcelain", *existing) if existing else ""
    if status:
        run_git("commit", "-m", message)
    else:
        print("Nothing new to commit -- continuing.")
    print("Pushing to GitHub...")
    run_git("push", "origin", branch)
    return branch


def raw_url(owner: str, repo: str, branch: str, path: Path) -> str:
    rel = path.relative_to(SCRIPT_DIR).as_posix()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rel}"


# ============================================================
# Buffer GraphQL client
# ============================================================

def graphql_request(token: str, query: str, variables: dict) -> dict:
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"Buffer API returned errors: {payload['errors']}")
    return payload["data"]


def get_organization_id(token: str) -> str:
    data = graphql_request(token, "query { account { organizations { id } } }", {})
    orgs = data["account"]["organizations"]
    if not orgs:
        raise RuntimeError("No organizations found on this Buffer account.")
    return orgs[0]["id"]


def get_instagram_channel_id(token: str, organization_id: str) -> str:
    data = graphql_request(
        token,
        """
        query GetChannels($organizationId: OrganizationId!) {
          channels(input: { organizationId: $organizationId }) {
            id
            displayName
            service
          }
        }
        """,
        {"organizationId": organization_id},
    )
    channels = data["channels"]
    instagram = [c for c in channels if c["service"] == "instagram"]
    if not instagram:
        raise RuntimeError("No Instagram channel found in this Buffer account.")
    return instagram[0]["id"]


CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post { id text }
    }
    ... on MutationError {
      message
    }
  }
}
"""

EDIT_POST_MUTATION = """
mutation EditPost($input: EditPostInput!) {
  editPost(input: $input) {
    ... on PostActionSuccess {
      post { id text }
    }
    ... on MutationError {
      message
    }
  }
}
"""


def create_post(token: str, channel_id: str, caption: str, image_urls: list[str]) -> dict:
    variables = {
        "input": {
            "text": caption,
            "channelId": channel_id,
            "schedulingType": "automatic",
            # Goes into the next open slot of the channel's posting schedule
            # in Buffer -- so Buffer's schedule, not the Action's run time,
            # decides when it actually appears on Instagram.
            "mode": "addToQueue",
            "saveToDraft": not AUTO_PUBLISH,
            "assets": [{"image": {"url": url}} for url in image_urls],
            # A carousel isn't its own Instagram post type -- it's a "post"
            # that happens to carry multiple images in `assets`.
            "metadata": {"instagram": {"type": "post", "shouldShareToFeed": True}},
        }
    }
    data = graphql_request(token, CREATE_POST_MUTATION, variables)
    return data["createPost"]


def edit_draft_post(token: str, post_id: str, caption: str, image_urls: list[str]) -> dict:
    variables = {
        "input": {
            "id": post_id,
            "text": caption,
            "assets": [{"image": {"url": url}} for url in image_urls],
            # Must match AUTO_PUBLISH, or revising a queued post would
            # silently demote it back to a draft and it'd never go out.
            "saveToDraft": not AUTO_PUBLISH,
        }
    }
    data = graphql_request(token, EDIT_POST_MUTATION, variables)
    return data["editPost"]


# ============================================================
# Slide image rendering
# ============================================================

SLIDE_SIZE = (1080, 1350)  # Instagram portrait 4:5
MARGIN = 96

# Four colour roles, reused on every post so the grid reads as one account.
# Every text/background pair here is measured at >= 4.5:1 -- see
# check_palette_contrast() below, which is a test, not an eyeball.
# The old mid-purple (#7B61FF) failed at 4.31:1 in both directions: too dark
# for dark text, too light for white. Split into a deep fill and a light
# accent instead.
DARK_BG = "#15151F"        # background
DARK_HEADING = "#F7F7FB"   # primary text            16.96:1 on DARK_BG
DARK_BODY = "#B8B8C8"      # secondary text           9.26:1 on DARK_BG
ACCENT_COLOR = "#A78BFF"   # accent (bars, dots)      6.71:1 on DARK_BG
ACCENT_BG = "#4F35C4"      # accent slide background
ACCENT_TEXT = "#F7F7FB"    # heading on accent         7.45:1 on ACCENT_BG
ACCENT_BODY = "#D8CEFF"    # body on accent            5.38:1 on ACCENT_BG
DIM_DOT = "#3A3A55"


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(fg: str, bg: str) -> float:
    la, lb = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check_palette_contrast(minimum: float = 4.5) -> list[str]:
    """Returns a list of failures, empty when the palette is compliant.
    Text over photos is handled separately -- text never sits directly on a
    photo, it sits on a solid panel, precisely so this stays provable."""
    pairs = [
        ("heading on dark", DARK_HEADING, DARK_BG),
        ("body on dark", DARK_BODY, DARK_BG),
        ("accent on dark", ACCENT_COLOR, DARK_BG),
        ("heading on accent", ACCENT_TEXT, ACCENT_BG),
        ("body on accent", ACCENT_BODY, ACCENT_BG),
    ]
    return [
        f"{name}: {contrast_ratio(fg, bg):.2f}:1 (need {minimum}:1)"
        for name, fg, bg in pairs
        if contrast_ratio(fg, bg) < minimum
    ]

_FONT_WARNED: set[bool] = set()


def _find_font_file(bold: bool) -> Path | None:
    suffix = "-Bold.ttf" if bold else "-Regular.ttf"
    if not FONT_DIR.exists():
        return None
    matches = sorted(FONT_DIR.glob(f"*{suffix}"))
    return matches[0] if matches else None


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
    return int(font.size * line_spacing) * len(lines)


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


def _vertical_gradient(size: tuple[int, int], top: str, bottom: str) -> Image.Image:
    """Flat colour blocks read as cheap; a subtle gradient costs nothing and
    gives the slide depth."""
    w, h = size
    top_rgb = tuple(int(top.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    bot_rgb = tuple(int(bottom.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    ramp = Image.new("RGB", (1, h))
    px = ramp.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(top_rgb[i] + (bot_rgb[i] - top_rgb[i]) * t) for i in range(3))
    return ramp.resize((w, h), Image.BILINEAR)


def _draw_text_panel(
    img: Image.Image,
    heading: str,
    body: str,
    panel_top: int,
    heading_color: str,
    body_color: str,
    bar_color: str,
    heading_size: int,
) -> None:
    """Lays the text out in the solid panel below the artwork. Text never
    sits directly on a photo -- that's what keeps the 4.5:1 contrast
    guarantee true regardless of what the photo happens to look like."""
    draw = ImageDraw.Draw(img)
    width, height = img.size
    content_width = width - 2 * MARGIN

    heading_font = _load_font(bold=True, size=heading_size)
    body_font = _load_font(bold=False, size=42)

    heading_lines = _wrap_by_pixels(draw, heading, heading_font, content_width)
    body_lines = _wrap_by_pixels(draw, body, body_font, content_width) if body else []

    gap_between = 36 if body_lines else 0
    block_height = (
        _text_block_height(heading_lines, heading_font, 1.12)
        + gap_between
        + _text_block_height(body_lines, body_font, 1.32)
    )

    panel_height = height - panel_top - 110  # leave room for the dot row
    y = panel_top + max(28, (panel_height - block_height - 30) // 2)

    draw.rectangle((MARGIN, y, MARGIN + 64, y + 6), fill=bar_color)
    y += 30

    y = _draw_block(draw, heading_lines, heading_font, MARGIN, y, heading_color, line_spacing=1.12)
    if body_lines:
        y += gap_between
        _draw_block(draw, body_lines, body_font, MARGIN, y, body_color, line_spacing=1.32)


def render_slide(
    index: int,
    total: int,
    heading: str,
    body: str,
    out_path: Path,
    artwork: "Image.Image | None" = None,
    style: str = "graphic",
) -> None:
    """style: 'photo' (full-bleed artwork up top), 'product' (artwork inset as
    a card, for screenshots that need a frame), or 'graphic' (no artwork)."""
    width, height = SLIDE_SIZE
    is_hook = index == 1
    is_cta = index == total
    accent_slide = (is_hook or is_cta) and artwork is None

    if accent_slide:
        base = _vertical_gradient(SLIDE_SIZE, ACCENT_BG, "#3B2699")
        heading_color, body_color = ACCENT_TEXT, ACCENT_BODY
        bar_color, dot_color, active_dot = ACCENT_TEXT, "#7C63D8", ACCENT_TEXT
    else:
        base = _vertical_gradient(SLIDE_SIZE, "#1B1B28", DARK_BG)
        heading_color, body_color = DARK_HEADING, DARK_BODY
        bar_color, dot_color, active_dot = ACCENT_COLOR, DIM_DOT, ACCENT_COLOR

    img = base
    # Text sits in roughly the same band on every slide, artwork or not, so
    # the set reads as one post while swiping. Without artwork the type goes
    # larger to fill the space with intent rather than leaving it empty.
    panel_top = int(height * 0.44)
    heading_size = 78 if is_hook else 58
    if artwork is None:
        heading_size += 14

    if artwork is not None and style == "photo":
        # Photo fills the upper 52%; text lives on solid colour beneath it.
        art_h = int(height * 0.52)
        img.paste(imagery.cover(artwork, (width, art_h)), (0, 0))
        # Short fade from photo into the panel so the seam isn't a hard line.
        fade_h = 90
        fade = _vertical_gradient((width, fade_h), "#1B1B28", DARK_BG)
        mask = Image.linear_gradient("L").resize((width, fade_h))
        img.paste(fade, (0, art_h - fade_h), mask)
        panel_top = art_h

    elif artwork is not None and style == "product":
        # Screenshots carry their own busy detail, so they get framed as a
        # card rather than bled to the edges.
        card_w = width - 2 * MARGIN
        card_h = int(card_w * 0.62)
        card_y = MARGIN + 20
        shot = imagery.cover(artwork, (card_w, card_h))
        rounded = Image.new("L", (card_w, card_h), 0)
        ImageDraw.Draw(rounded).rounded_rectangle((0, 0, card_w - 1, card_h - 1), radius=28, fill=255)
        img.paste(shot, (MARGIN, card_y), rounded)
        ImageDraw.Draw(img).rounded_rectangle(
            (MARGIN, card_y, MARGIN + card_w - 1, card_y + card_h - 1),
            radius=28, outline=ACCENT_COLOR, width=3,
        )
        panel_top = card_y + card_h

    _draw_text_panel(
        img, heading, body, panel_top,
        heading_color, body_color, bar_color, heading_size,
    )
    _draw_progress_dots(ImageDraw.Draw(img), index, total, width, height, dot_color, active_dot)
    img.save(out_path)


def clear_old_slides(post_dir: Path) -> None:
    """Removes any existing slide_*.png before re-rendering, so a revision
    that changes the slide count doesn't leave stale extra files behind."""
    for old_slide in post_dir.glob("slide_*.png"):
        old_slide.unlink()


def _product_view(shot: Image.Image, n: int) -> Image.Image:
    """A post often has several product slides but the site offers one hero
    image. Showing it whole every time looks lazy, so each successive product
    slide zooms into a different region of it (a different panel of the UI)."""
    w, h = shot.size
    views = [
        (0, 0, w, h),                                   # whole image
        (0, 0, int(w * 0.62), int(h * 0.62)),           # top-left
        (int(w * 0.38), int(h * 0.38), w, h),           # bottom-right
        (int(w * 0.38), 0, w, int(h * 0.62)),           # top-right
        (0, int(h * 0.38), int(w * 0.62), h),           # bottom-left
        (int(w * 0.19), int(h * 0.19), int(w * 0.81), int(h * 0.81)),  # centre
    ]
    return shot.crop(views[n % len(views)])


def render_all_slides(post: dict, post_dir: Path) -> list[Path]:
    clear_old_slides(post_dir)
    slides = post["slides"]

    # One fetch of the product shot, reused across the slides that want it --
    # no point hitting the vendor's site once per slide.
    product_shot = None
    if any(s.get("image_kind") == "product" for s in slides):
        product_shot = imagery.og_image(post.get("tool_url", ""))
        if product_shot is None:
            print("    (no product shot available -- those slides fall back to graphics)")

    paths = []
    product_count = 0
    for i, slide in enumerate(slides, start=1):
        kind = slide.get("image_kind", "graphic")
        artwork, style = None, "graphic"

        if kind == "product" and product_shot is not None:
            artwork, style = _product_view(product_shot, product_count), "product"
            product_count += 1
        elif kind == "photo":
            artwork = imagery.stock_photo(slide.get("image_query", ""))
            if artwork is not None:
                style = "photo"

        out_path = post_dir / f"slide_{i:02d}.png"
        render_slide(
            i, len(slides), slide["heading"], slide["body"], out_path,
            artwork=artwork, style=style,
        )
        paths.append(out_path)
        try:
            shown = out_path.relative_to(SCRIPT_DIR)
        except ValueError:
            shown = out_path
        print(f"  wrote {shown}  [{style}]")
    return paths


# ============================================================
# Post folder helpers
# ============================================================

def find_post_dir(requested_date: str | None) -> Path:
    if requested_date:
        post_dir = POSTS_DIR / requested_date
        if not post_dir.exists():
            raise RuntimeError(f"No folder found at {post_dir}")
        return post_dir
    candidates = sorted(p for p in POSTS_DIR.iterdir() if p.is_dir())
    if not candidates:
        raise RuntimeError(f"No post folders found in {POSTS_DIR}. Run generate_content.py first.")
    return candidates[-1]


def load_content(post_dir: Path) -> dict:
    return json.loads((post_dir / "content.json").read_text())


def save_content(post_dir: Path, content: dict) -> None:
    (post_dir / "content.json").write_text(json.dumps(content, indent=2))

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

# --- Shared locations ---
SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_PATH = SCRIPT_DIR / "history.json"
POSTS_DIR = SCRIPT_DIR / "posts"
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
    "- Include one honest limitation, briefly, near the end. Credibility is what makes the "
    "inspiring parts believable -- but don't dwell on it.\n"
    f"- Banned words/phrases -- do not use any of these anywhere: {', '.join(BANNED_PHRASES)}.\n"
    "- caption: an Instagram caption (under 2200 characters). The FIRST LINE must be a "
    "scroll-stopping hook -- a specific claim, number, or contrarian observation, not a "
    "generic opener like 'Have you heard of...' or 'Let's talk about...'. "
    f"{_DISCLOSURE_RULE} Do NOT include "
    "any raw URLs in the caption -- Instagram won't make them clickable anyway.\n"
    "- slides: produce 5 to 7 carousel slides, following this arc:\n"
    "    1. HOOK -- works alone as a thumbnail. Under 8 words. The surprising promise or "
    "the 'wait, that exists?' moment.\n"
    "    2. THE STATUS QUO -- the tedious, annoying, or slow thing the reader currently "
    "does. Make them feel it.\n"
    "    3-5. THE GOOD PART -- each slide is one vivid capability, written as an outcome "
    "('ask it what you decided three weeks ago and it just answers'), not a feature label. "
    "At least one of these should land the 'I didn't know that was possible' feeling.\n"
    "    Second-to-last. THE HONEST CATCH -- one real limitation, one sentence.\n"
    "    Last. CTA -- what to do next, pointing to the bio link.\n"
    "  No slide may be a pricing table. Each needs a short heading (under 40 characters) "
    "and a 1-2 sentence body written to fit on a graphic, not a paragraph.\n"
    "- hashtags: 5-10 relevant hashtags, no '#' symbol included.\n"
    "- sources: the URLs you used for factual claims, so a human can spot-check them. At "
    "least one should be independent of the vendor (a review, a press piece, a "
    "Reddit/Hacker News/Product Hunt discussion) where one exists.\n"
)


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
DARK_BG = "#15151F"
DARK_HEADING = "#F7F7FB"
DARK_BODY = "#B8B8C8"
ACCENT_COLOR = "#7B61FF"
ACCENT_BG = "#7B61FF"
ACCENT_TEXT = "#15151F"
ACCENT_BODY = "#2B2350"
MARGIN = 96

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

    usable_height = height - MARGIN - 120
    y = MARGIN + max(0, (usable_height - block_height) // 2)

    bar_color = ACCENT_TEXT if accent_slide else ACCENT_COLOR
    draw.rectangle((MARGIN, y, MARGIN + 64, y + 6), fill=bar_color)
    y += 30

    y = _draw_block(draw, heading_lines, heading_font, MARGIN, y, heading_color, line_spacing=1.15)
    y += gap_between
    _draw_block(draw, body_lines, body_font, MARGIN, y, body_color, line_spacing=1.3)

    _draw_progress_dots(draw, index, total, width, height, dot_color, active_dot)

    img.save(out_path)


def clear_old_slides(post_dir: Path) -> None:
    """Removes any existing slide_*.png before re-rendering, so a revision
    that changes the slide count doesn't leave stale extra files behind."""
    for old_slide in post_dir.glob("slide_*.png"):
        old_slide.unlink()


def render_all_slides(post: dict, post_dir: Path) -> list[Path]:
    clear_old_slides(post_dir)
    slides = post["slides"]
    paths = []
    for i, slide in enumerate(slides, start=1):
        out_path = post_dir / f"slide_{i:02d}.png"
        render_slide(i, len(slides), slide["heading"], slide["body"], out_path)
        paths.append(out_path)
        print(f"  wrote {out_path.relative_to(SCRIPT_DIR)}")
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

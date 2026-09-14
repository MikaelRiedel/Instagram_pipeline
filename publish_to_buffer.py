"""
Buffer publish script.

What this does:
  1. Reads the latest generated post from posts/<date>/ (content.json + slide
     images), or a specific date if you pass one
  2. Commits and pushes those images to THIS repo's GitHub remote, so they
     get a public raw.githubusercontent.com URL Buffer can fetch
  3. Creates a DRAFT Instagram carousel post in Buffer using those image
     URLs and the caption (+ hashtags appended)

Like the earlier test script, this uses saveToDraft: true -- nothing
publishes to Instagram automatically. It lands as a draft in Buffer for you
to review and send yourself.

Requirements:
  - This script must live INSIDE your cloned GitHub repo (the one connected
    to Buffer's image URLs), not in Downloads or anywhere else
  - That GitHub repo must be PUBLIC -- Buffer needs to fetch the images over
    the open internet with no login; a private repo's raw URLs won't work
  - `git push` must already work from this folder without any extra prompts
    (test with a plain `git push` first if you're not sure)

Setup (once):
    pip install requests
    export BUFFER_ACCESS_TOKEN="your-buffer-token"

Run:
    python3 publish_to_buffer.py            # publishes the most recent posts/<date> folder
    python3 publish_to_buffer.py 2026-09-14  # publishes a specific date instead
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
POSTS_DIR = SCRIPT_DIR / "posts"
API_URL = "https://api.buffer.com"


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


def commit_and_push(post_dir: Path) -> str:
    """Commits the post's folder if needed, pushes, returns the current branch."""
    branch = run_git("symbolic-ref", "--short", "HEAD")
    run_git("add", str(post_dir))
    status = run_git("status", "--porcelain", str(post_dir))
    if status:
        run_git("commit", "-m", f"Add content for {post_dir.name}")
    else:
        print("Nothing new to commit in this folder -- continuing.")
    print("Pushing to GitHub...")
    run_git("push", "origin", branch)
    return branch


def raw_url(owner: str, repo: str, branch: str, path: Path) -> str:
    rel = path.relative_to(SCRIPT_DIR).as_posix()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rel}"


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


def create_carousel_draft(token: str, channel_id: str, caption: str, image_urls: list[str]) -> dict:
    variables = {
        "input": {
            "text": caption,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": "addToQueue",
            "saveToDraft": True,
            "assets": [{"image": {"url": url}} for url in image_urls],
            "metadata": {"instagram": {"type": "post", "shouldShareToFeed": True}},
        }
    }
    data = graphql_request(token, CREATE_POST_MUTATION, variables)
    return data["createPost"]


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


def build_caption(content: dict) -> str:
    hashtags = " ".join(f"#{tag}" for tag in content.get("hashtags", []))
    if hashtags:
        return f"{content['caption']}\n\n{hashtags}"
    return content["caption"]


def main() -> None:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print('Set BUFFER_ACCESS_TOKEN first, e.g.:\n  export BUFFER_ACCESS_TOKEN="your-token-here"')
        sys.exit(1)

    requested_date = sys.argv[1] if len(sys.argv) > 1 else None
    post_dir = find_post_dir(requested_date)
    print(f"Using: {post_dir.relative_to(SCRIPT_DIR)}")

    content = json.loads((post_dir / "content.json").read_text())
    slide_paths = sorted(post_dir.glob("slide_*.png"))
    if not slide_paths:
        raise RuntimeError(f"No slide_*.png files found in {post_dir}")

    owner, repo = get_repo_slug()
    branch = commit_and_push(post_dir)

    image_urls = [raw_url(owner, repo, branch, p) for p in slide_paths]
    print("\nImage URLs Buffer will use:")
    for url in image_urls:
        print(f"  {url}")

    caption = build_caption(content)

    print("\nLooking up your Buffer organization and Instagram channel...")
    org_id = get_organization_id(token)
    channel_id = get_instagram_channel_id(token, org_id)

    print("Creating a DRAFT carousel post (will NOT publish automatically)...")
    result = create_carousel_draft(token, channel_id, caption, image_urls)

    if "message" in result:
        print(f"Buffer rejected the post: {result['message']}")
        sys.exit(1)

    print(f"\nSuccess! Draft post created (id: {result['post']['id']})")
    print(f"Topic: {content['tool_name']}")
    print("Open the Buffer app to review and send it -- nothing is live yet.")


if __name__ == "__main__":
    main()

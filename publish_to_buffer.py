"""
Buffer publish script.

What this does:
  1. Reads the latest generated post from posts/<date>/ (content.json + slide
     images), or a specific date if you pass one
  2. Commits and pushes those images (+ history.json) to THIS repo's GitHub
     remote, so they get a public raw.githubusercontent.com URL Buffer can
     fetch
  3. Creates a DRAFT Instagram carousel post in Buffer using those image
     URLs and the caption (+ hashtags appended), and saves the returned
     post ID into the post's folder so revise_content.py can later edit
     this exact draft instead of creating a duplicate

Like the earlier test script, this uses saveToDraft: true -- nothing
publishes to Instagram automatically. It lands as a draft in Buffer for you
to review and send yourself.

Requirements:
  - This script must live INSIDE your cloned GitHub repo, not in Downloads
    or anywhere else
  - That GitHub repo must be PUBLIC -- Buffer needs to fetch the images over
    the open internet with no login; a private repo's raw URLs won't work
  - `git push` must already work from this folder without any extra prompts

Setup (once):
    pip install requests
    export BUFFER_ACCESS_TOKEN="your-buffer-token"

Run:
    python3 publish_to_buffer.py            # publishes the most recent posts/<date> folder
    python3 publish_to_buffer.py 2026-09-14  # publishes a specific date instead
"""

from __future__ import annotations

import os
import sys

from pipeline_common import (
    HISTORY_PATH,
    SCRIPT_DIR,
    build_caption,
    commit_and_push,
    create_draft_post,
    find_post_dir,
    get_instagram_channel_id,
    get_organization_id,
    get_repo_slug,
    load_content,
    raw_url,
)


def main() -> None:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print('Set BUFFER_ACCESS_TOKEN first, e.g.:\n  export BUFFER_ACCESS_TOKEN="your-token-here"')
        sys.exit(1)

    requested_date = sys.argv[1] if len(sys.argv) > 1 else None
    post_dir = find_post_dir(requested_date)
    print(f"Using: {post_dir.relative_to(SCRIPT_DIR)}")

    content = load_content(post_dir)
    slide_paths = sorted(post_dir.glob("slide_*.png"))
    if not slide_paths:
        raise RuntimeError(f"No slide_*.png files found in {post_dir}")

    owner, repo = get_repo_slug()
    branch = commit_and_push(
        [post_dir, HISTORY_PATH], f"Add content for {post_dir.name}"
    )

    image_urls = [raw_url(owner, repo, branch, p) for p in slide_paths]
    print("\nImage URLs Buffer will use:")
    for url in image_urls:
        print(f"  {url}")

    caption = build_caption(content)

    print("\nLooking up your Buffer organization and Instagram channel...")
    org_id = get_organization_id(token)
    channel_id = get_instagram_channel_id(token, org_id)

    print("Creating a DRAFT carousel post (will NOT publish automatically)...")
    result = create_draft_post(token, channel_id, caption, image_urls)

    if "message" in result:
        print(f"Buffer rejected the post: {result['message']}")
        sys.exit(1)

    post_id = result["post"]["id"]
    (post_dir / "buffer_post_id.txt").write_text(post_id)

    print(f"\nSuccess! Draft post created (id: {post_id})")
    print(f"Topic: {content['tool_name']}")
    print("Open the Buffer app to review and send it -- nothing is live yet.")
    print(f"\nIf you want changes later, run:")
    print(f"  python3 revise_content.py {post_dir.name} \"your feedback here\"")


if __name__ == "__main__":
    main()

"""
Revise an already-generated post based on your feedback.

What this does:
  1. Loads posts/<date>/content.json (a post generate_content.py already
     wrote, and ideally already pushed to Buffer as a draft)
  2. Sends it + your feedback to Claude, asking for a revised version that
     specifically addresses what you said
  3. Re-renders the slide images from the revised text
  4. Commits + pushes the updated images to GitHub
  5. Edits the EXISTING Buffer draft in place (via Buffer's editPost) so
     you get one updated draft, not a second duplicate one

The previous version of content.json is saved as content_before_feedback.json
in the same folder, so you can always see what changed.

Requires: posts/<date>/buffer_post_id.txt must exist, which means you need
to have run publish_to_buffer.py for this date at least once already.

Setup (once):
    pip install anthropic pillow requests
    export ANTHROPIC_API_KEY="your-key-here"
    export BUFFER_ACCESS_TOKEN="your-buffer-token"

Run:
    python3 revise_content.py 2026-09-14 "the hook is boring, make it punchier and cut slide 4"
"""

from __future__ import annotations

import json
import os
import sys

import anthropic

from pipeline_common import (
    _structured_json,
    playbook_prompt_block,
    MODEL,
    POST_SCHEMA,
    SCRIPT_DIR,
    WRITING_RULES,
    build_caption,
    commit_and_push,
    edit_draft_post,
    find_post_dir,
    get_repo_slug,
    load_content,
    raw_url,
    render_all_slides,
    save_content,
)


def revise_with_feedback(client: anthropic.Anthropic, current_post: dict, feedback: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        output_config={"format": {"type": "json_schema", "schema": POST_SCHEMA}},
        messages=[{
            "role": "user",
            "content": (
                "Here is the current version of an Instagram post:\n\n"
                f"{json.dumps(current_post, indent=2)}\n\n"
                "The person reviewing it gave this feedback:\n"
                f'"{feedback}"\n\n'
                "Revise the post to directly address that feedback. Keep everything that "
                "wasn't mentioned as close to the original as makes sense -- don't rewrite "
                "parts that weren't flagged just for the sake of it, unless the feedback "
                f"requires it.\n\n{WRITING_RULES}{playbook_prompt_block()}\n"
                "Return ONLY the revised post in the schema."
            ),
        }],
    )
    return _structured_json(response)


def main() -> None:
    if len(sys.argv) < 3:
        print('Usage: python3 revise_content.py <date> "your feedback here"')
        print('Example: python3 revise_content.py 2026-09-14 "hook is weak, punch it up"')
        sys.exit(1)

    requested_date, feedback = sys.argv[1], sys.argv[2]

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    buffer_token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not anthropic_key:
        print('Set ANTHROPIC_API_KEY first, e.g.:\n  export ANTHROPIC_API_KEY="your-key-here"')
        sys.exit(1)
    if not buffer_token:
        print('Set BUFFER_ACCESS_TOKEN first, e.g.:\n  export BUFFER_ACCESS_TOKEN="your-token-here"')
        sys.exit(1)

    post_dir = find_post_dir(requested_date)
    print(f"Using: {post_dir.relative_to(SCRIPT_DIR)}")

    post_id_path = post_dir / "buffer_post_id.txt"
    if not post_id_path.exists():
        print(
            f"No {post_id_path.relative_to(SCRIPT_DIR)} found -- this post was never "
            "published to Buffer yet, so there's no draft to edit.\n"
            "Run publish_to_buffer.py for this date first, then revise it."
        )
        sys.exit(1)
    post_id = post_id_path.read_text().strip()

    current_content = load_content(post_dir)

    print(f"\nApplying feedback: \"{feedback}\"")
    client = anthropic.Anthropic(max_retries=0, timeout=600.0)
    revised = revise_with_feedback(client, current_content, feedback)

    # Keep the previous version around so you can see what changed.
    (post_dir / "content_before_feedback.json").write_text(json.dumps(current_content, indent=2))
    save_content(post_dir, revised)

    print("\nRe-rendering slides...")
    render_all_slides(revised, post_dir)

    print("\nPushing updated images to GitHub...")
    owner, repo = get_repo_slug()
    branch = commit_and_push([post_dir], f"Revise content for {post_dir.name}: {feedback[:60]}")

    slide_paths = sorted(post_dir.glob("slide_*.png"))
    image_urls = [raw_url(owner, repo, branch, p) for p in slide_paths]

    caption = build_caption(revised)

    print("Updating the existing Buffer draft (not creating a new one)...")
    result = edit_draft_post(buffer_token, post_id, caption, image_urls)

    if "message" in result:
        print(f"Buffer rejected the edit: {result['message']}")
        sys.exit(1)

    print(f"\nDone. Draft post {post_id} updated with your feedback applied.")
    print("Open Buffer to review the new version -- still nothing live.")


if __name__ == "__main__":
    main()

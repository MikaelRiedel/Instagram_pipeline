"""
Nightly stats loop.

Pulls engagement for recently published posts, looks for patterns that hold
across several of them, and may append ONE directive to project/playbook.md
plus one entry to project/log.md. It edits markdown only -- never code.

Three rules are enforced in Python rather than left to the model, because a
model asked "find a pattern" will always find one:

  - A pattern needs at least MIN_POSTS_PER_PATTERN posts. One good post is noise.
  - Posts younger than SETTLE_HOURS are excluded; engagement hasn't settled.
  - Below MIN_SIGNAL_* the loop refuses to conclude anything at all. On
    2026-09-18 this account's posts had reach 1-2 and zero saves, and inventing
    directives from that would poison the playbook for months.

The model's only job is to phrase a directive once the evidence already exists.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from pipeline_common import (  # noqa: E402
    MODEL,
    PLAYBOOK_PATH,
    POSTS_LOG_PATH,
    PROJECT_DIR,
    get_instagram_channel_id,
    get_organization_id,
    graphql_request,
    _structured_json,
)

LOG_PATH = PROJECT_DIR / "log.md"

LOOKBACK_DAYS = 14
SETTLE_HOURS = 72
MIN_POSTS_PER_PATTERN = 3
MAX_DIRECTIVES = 20
# Below these, there is no audience to learn from.
MIN_SIGNAL_TOTAL_REACH = 100
MIN_SIGNAL_TOTAL_SAVES = 3

POSTS_QUERY = """
query GetPostsWithMetrics($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(
    first: 50
    input: {
      organizationId: $organizationId
      filter: { status: [sent], channelIds: [$channelId] }
    }
  ) {
    edges { node { id dueAt text metrics { name value } } }
  }
}
"""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "none"]},
        "directive": {"type": "string"},
        "reason": {"type": "string"},
        "retire": {"type": "array", "items": {"type": "string"}},
        "log_note": {"type": "string"},
    },
    "required": ["action", "directive", "reason", "retire", "log_note"],
    "additionalProperties": False,
}


def fetch_posts(token: str) -> list[dict]:
    org = get_organization_id(token)
    channel = get_instagram_channel_id(token, org)
    data = graphql_request(token, POSTS_QUERY, {"organizationId": org, "channelId": channel})
    out = []
    for edge in data["posts"]["edges"]:
        node = edge["node"]
        metrics = {m["name"]: m.get("value") or 0 for m in (node.get("metrics") or [])}
        out.append({"id": node["id"], "dueAt": node.get("dueAt"),
                    "text": node.get("text") or "", "metrics": metrics})
    return out


def eligible(posts: list[dict]) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    keep = []
    for p in posts:
        if not p["dueAt"]:
            continue
        try:
            due = dt.datetime.fromisoformat(p["dueAt"].replace("Z", "+00:00"))
        except ValueError:
            continue
        age_h = (now - due).total_seconds() / 3600
        if SETTLE_HOURS <= age_h <= LOOKBACK_DAYS * 24:
            p["age_hours"] = round(age_h)
            keep.append(p)
    return keep


def load_post_meta() -> dict[str, dict]:
    """post_id -> what we recorded at generation time."""
    if not POSTS_LOG_PATH.exists():
        return {}
    meta = {}
    for line in POSTS_LOG_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entry = json.loads(line)
                meta[entry["post_id"]] = entry
            except json.JSONDecodeError:
                continue
    return meta


def group_stats(posts: list[dict], meta: dict[str, dict], key: str) -> dict[str, dict]:
    """Aggregates by a generation-time attribute, keeping only groups with
    enough posts to be worth believing."""
    buckets: dict[str, list[dict]] = {}
    for p in posts:
        attr = (meta.get(p["id"]) or {}).get(key)
        if attr:
            buckets.setdefault(str(attr), []).append(p)
    out = {}
    for name, group in buckets.items():
        if len(group) < MIN_POSTS_PER_PATTERN:
            continue
        out[name] = {
            "n": len(group),
            "mean_saves": round(statistics.mean(g["metrics"].get("Saves", 0) for g in group), 2),
            "mean_reach": round(statistics.mean(g["metrics"].get("Reach", 0) for g in group), 2),
            "mean_shares": round(statistics.mean(g["metrics"].get("Shares", 0) for g in group), 2),
        }
    return out


def append_log(entry: str) -> None:
    body = LOG_PATH.read_text() if LOG_PATH.exists() else "# Log\n\nAppend-only. Newest at top.\n"
    marker = "Append-only. Newest at top.\n"
    head, _, tail = body.partition(marker)
    LOG_PATH.write_text(f"{head}{marker}\n{entry.strip()}\n{tail}")


def apply_to_playbook(directive: str, reason: str, retire: list[str]) -> None:
    text = PLAYBOOK_PATH.read_text()
    for old in retire:
        for line in text.splitlines():
            if line.startswith("- ") and old.strip()[:50] in line:
                # Drop the bullet and its indented reason lines.
                block, keep, dropping = [], [], False
                for cur in text.splitlines():
                    if cur == line:
                        dropping = True
                        continue
                    if dropping and (cur.startswith("  ") or not cur.strip()):
                        continue
                    dropping = False
                    keep.append(cur)
                text = "\n".join(keep)
                break
    today = dt.date.today().isoformat()
    text = text.rstrip() + f"\n\n- {today} — {directive}\n  *Reason: {reason}*\n"
    PLAYBOOK_PATH.write_text(text)


def main() -> None:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token or not os.environ.get("ANTHROPIC_API_KEY"):
        print("Missing BUFFER_ACCESS_TOKEN or ANTHROPIC_API_KEY")
        sys.exit(1)

    posts = eligible(fetch_posts(token))
    total_reach = sum(p["metrics"].get("Reach", 0) for p in posts)
    total_saves = sum(p["metrics"].get("Saves", 0) for p in posts)
    print(f"eligible posts: {len(posts)}  total reach: {total_reach}  total saves: {total_saves}")

    if len(posts) < MIN_POSTS_PER_PATTERN or (
        total_reach < MIN_SIGNAL_TOTAL_REACH and total_saves < MIN_SIGNAL_TOTAL_SAVES
    ):
        append_log(
            f"## {dt.date.today().isoformat()} — stats loop\n"
            f"Insufficient signal: {len(posts)} settled posts, {total_reach} total reach, "
            f"{total_saves} total saves. Needs {MIN_SIGNAL_TOTAL_REACH} reach or "
            f"{MIN_SIGNAL_TOTAL_SAVES} saves before any pattern is believable.\n"
            f"No directive added. Reach, not copy, is the bottleneck."
        )
        print("Insufficient signal -- logged, playbook untouched.")
        return

    meta = load_post_meta()
    evidence = {
        "posts_analysed": len(posts),
        "overall": {
            "mean_saves": round(statistics.mean(p["metrics"].get("Saves", 0) for p in posts), 2),
            "mean_reach": round(statistics.mean(p["metrics"].get("Reach", 0) for p in posts), 2),
        },
        "by_hook_type": group_stats(posts, meta, "hook_type"),
        "by_format": group_stats(posts, meta, "format"),
        "top_posts": sorted(
            [{"saves": p["metrics"].get("Saves", 0), "reach": p["metrics"].get("Reach", 0),
              "hook": (meta.get(p["id"]) or {}).get("hook_type"), "text": p["text"][:110]}
             for p in posts],
            key=lambda x: x["saves"], reverse=True)[:5],
    }

    current = PLAYBOOK_PATH.read_text()
    directive_count = sum(1 for l in current.splitlines() if l.startswith("- 20"))

    client = anthropic.Anthropic(max_retries=0, timeout=300.0)
    resp = client.messages.create(
        model=MODEL, max_tokens=2000,
        output_config={"format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
        messages=[{"role": "user", "content": (
            "You maintain the playbook for an Instagram account that reviews AI tools. "
            "The playbook is an INPUT to post generation, so every directive changes "
            "future posts.\n\nMeasured evidence from settled posts (72h+ old):\n"
            f"{json.dumps(evidence, indent=2)}\n\nCurrent playbook:\n{current}\n\n"
            "Add AT MOST ONE directive, and only if the evidence genuinely supports it "
            "across at least 3 posts. A difference driven by one strong post is noise -- "
            "in that case return action 'none'. Prefer 'none' over a weak directive: a "
            "wrong directive actively degrades every future post.\n"
            f"The playbook currently holds {directive_count} directives; the cap is "
            f"{MAX_DIRECTIVES}. If adding would exceed it, name the weakest existing "
            "directives in 'retire' (quote their opening words) and say why in log_note.\n"
            "directive: one imperative sentence, the instruction only. reason: one line "
            "citing the numbers. log_note: 2-3 lines for the human-readable log."
        )}],
    )
    decision = _structured_json(resp)

    if decision["action"] == "none":
        append_log(f"## {dt.date.today().isoformat()} — stats loop\n"
                   f"Analysed {len(posts)} settled posts. No directive added.\n"
                   f"{decision['log_note']}")
        print("No directive warranted.")
        return

    apply_to_playbook(decision["directive"], decision["reason"], decision.get("retire", []))
    append_log(f"## {dt.date.today().isoformat()} — stats loop\n"
               f"Analysed {len(posts)} settled posts. Added directive: "
               f"{decision['directive']}\n{decision['log_note']}")
    print(f"Added directive: {decision['directive']}")


if __name__ == "__main__":
    main()

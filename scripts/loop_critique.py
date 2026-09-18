"""
Daily critique loop.

This one does not optimise. It argues.

The stats loop tunes what already exists; this one asks whether the right
thing exists at all -- format mix, cadence, distribution, positioning, whether
a daily carousel is even the right shape for this business. It is explicitly
allowed to contradict project/playbook.md and to propose work that is not
posting.

Output is GitHub issues labelled `critique`, never commits and never code.
Capped at 3 per run, and it reads open issues first so it doesn't file the
same complaint twice.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from pipeline_common import (  # noqa: E402
    MODEL,
    POSTS_DIR,
    PROJECT_DIR,
    SCRIPT_DIR,
    get_instagram_channel_id,
    get_organization_id,
    graphql_request,
    _structured_json,
)

LOG_PATH = PROJECT_DIR / "log.md"
MAX_ISSUES = 3
LOOKBACK_DAYS = 14

METRICS_QUERY = """
query GetPostsWithMetrics($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(
    first: 50
    input: { organizationId: $organizationId, filter: { status: [sent], channelIds: [$channelId] } }
  ) { edges { node { id dueAt text metrics { name value } } } }
}
"""

ISSUES_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "where": {"type": "string"},
                    "current_behaviour": {"type": "string"},
                    "proposed_change": {"type": "string"},
                    "why": {"type": "string"},
                    "first_step": {"type": "string"},
                    "expected_impact": {"type": "string", "enum": ["high", "medium", "low"]},
                    "effort_hours": {"type": "number"},
                },
                "required": ["title", "where", "current_behaviour", "proposed_change",
                             "why", "first_step", "expected_impact", "effort_hours"],
                "additionalProperties": False,
            },
        },
        "overall_argument": {"type": "string"},
    },
    "required": ["issues", "overall_argument"],
    "additionalProperties": False,
}


def read(path: Path, limit: int = 6000) -> str:
    return path.read_text()[:limit] if path.exists() else "(missing)"


def recent_posts() -> list[dict]:
    cutoff = dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)
    out = []
    for folder in sorted(POSTS_DIR.iterdir()) if POSTS_DIR.exists() else []:
        if not folder.is_dir():
            continue
        try:
            if dt.date.fromisoformat(folder.name) < cutoff:
                continue
        except ValueError:
            continue
        content = folder / "content.json"
        if content.exists():
            data = json.loads(content.read_text())
            out.append({
                "date": folder.name,
                "tool": data.get("tool_name"),
                "hook": (data.get("slides") or [{}])[0].get("heading"),
                "hook_type": data.get("hook_type"),
                "caption_first_line": (data.get("caption") or "").split("\n")[0],
                "slide_headings": [s.get("heading") for s in data.get("slides", [])],
            })
    return out


def live_metrics() -> list[dict]:
    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        return []
    try:
        org = get_organization_id(token)
        channel = get_instagram_channel_id(token, org)
        data = graphql_request(token, METRICS_QUERY,
                               {"organizationId": org, "channelId": channel})
        return [{
            "dueAt": e["node"].get("dueAt"),
            "text": (e["node"].get("text") or "")[:80],
            **{m["name"]: m.get("value") or 0 for m in (e["node"].get("metrics") or [])},
        } for e in data["posts"]["edges"]]
    except Exception as exc:  # metrics are useful, not essential, to this loop
        print(f"(metrics unavailable: {type(exc).__name__})")
        return []


def open_critique_issues() -> list[str]:
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--label", "critique", "--state", "open",
             "--limit", "50", "--json", "title"],
            capture_output=True, text=True, check=True, cwd=SCRIPT_DIR,
        ).stdout
        return [i["title"] for i in json.loads(out)]
    except Exception as exc:
        print(f"(could not list issues: {exc})")
        return []


def create_issue(issue: dict, argument: str) -> None:
    body = f"""**Where:** {issue['where']}

**What it does now**
{issue['current_behaviour']}

**What it should do instead**
{issue['proposed_change']}

**Why**
{issue['why']}

**First step**
{issue['first_step']}

**Expected impact:** {issue['expected_impact']} · **Estimated effort:** {issue['effort_hours']}h

---
*Filed by the critique loop on {dt.date.today().isoformat()}. Its overall argument this run:*
> {argument}
"""
    subprocess.run(
        ["gh", "issue", "create", "--title", issue["title"], "--body", body,
         "--label", "critique"],
        check=True, cwd=SCRIPT_DIR,
    )


def append_log(entry: str) -> None:
    body = LOG_PATH.read_text() if LOG_PATH.exists() else "# Log\n\nAppend-only. Newest at top.\n"
    marker = "Append-only. Newest at top.\n"
    head, _, tail = body.partition(marker)
    LOG_PATH.write_text(f"{head}{marker}\n{entry.strip()}\n{tail}")


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Missing ANTHROPIC_API_KEY")
        sys.exit(1)

    posts = recent_posts()
    metrics = live_metrics()
    existing = open_critique_issues()

    prompt = f"""You are the strategist for a small Instagram business, and you are
expected to become genuinely expert at growing this kind of account. You are not a
copy editor. Your job is to work out what this business should DO next -- which may
be about content, but may equally be about distribution, format mix, posting cadence,
positioning, audience, partnerships, or something nobody here has thought of yet.

Be open-minded and specific. Do not limit yourself to the ideas already mentioned in
the repo. If the current strategy is wrong at the root, say that.

GOAL
{read(PROJECT_DIR / 'goal.md')}

CONSTRAINTS (you must respect these)
{read(PROJECT_DIR / 'constraints.md')}

CURRENT PLAYBOOK (you may disagree with any of it, and should say so if you do)
{read(PROJECT_DIR / 'playbook.md')}

ALREADY KNOWN AND OPEN WITH THE OWNER
{read(PROJECT_DIR / 'open.md')}

THE LAST {LOOKBACK_DAYS} DAYS OF POSTS
{json.dumps(posts, indent=2)[:6000]}

ACTUAL PERFORMANCE FROM INSTAGRAM (via Buffer)
{json.dumps(metrics, indent=2)[:3000]}

HOW POSTS ARE GENERATED (the code that would need changing)
{read(SCRIPT_DIR / 'generate_content.py', 7000)}

OPEN CRITIQUE ISSUES -- do not refile these, but you may build on them:
{json.dumps(existing, indent=2)}

Research current practice on the open web before answering. Look for what actually
works for accounts in this position right now, not evergreen advice. Then argue.

Return at most {MAX_ISSUES} issues, ordered by expected impact per hour of work,
highest first. Fewer is better than padding. Each must be concrete enough to act on
tomorrow. 'where' should name the file and function when code is involved, or
'strategy - no code' when it isn't. Judgement beats percentages: "your hooks are all
the same shape" is more useful than a statistic.

overall_argument: two or three sentences on what you think is actually going on with
this business right now."""

    client = anthropic.Anthropic(max_retries=0, timeout=600.0)
    with client.messages.stream(
        # Generous: a web-search loop spends output tokens on every turn, and
        # the final JSON has to fit in what's left. 8000 ran out mid-object.
        model=MODEL, max_tokens=16000,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
        output_config={"format": {"type": "json_schema", "schema": ISSUES_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    result = _structured_json(response)
    issues = result["issues"][:MAX_ISSUES]
    print(f"argument: {result['overall_argument']}\n")
    for issue in issues:
        print(f"filing: [{issue['expected_impact']}] {issue['title']}")
        create_issue(issue, result["overall_argument"])

    append_log(
        f"## {dt.date.today().isoformat()} — critique loop\n"
        f"Reviewed {len(posts)} recent posts and live metrics; filed {len(issues)} issue(s).\n"
        f"Argument: {result['overall_argument']}"
    )


if __name__ == "__main__":
    main()

"""
Morning brief across the portfolio.

Walks every project in portfolio/projects.yml, reads its log.md and open.md,
and writes one short markdown file meant to be read aloud over coffee. Not a
dashboard: no tables, no metric dumps, no nested bullets.

Delivers to Google Drive when a service-account key is configured, and
otherwise commits to briefs/ in this repo and says so.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from pipeline_common import MODEL, SCRIPT_DIR, _structured_json  # noqa: E402

PROJECTS_FILE = SCRIPT_DIR / "portfolio" / "projects.yml"
BRIEFS_DIR = SCRIPT_DIR / "briefs"
DRIVE_FOLDER = "Personal Assistant/output/briefs"

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_you": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "item": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "cost_of_ignoring": {"type": "string"},
                },
                "required": ["project", "item", "recommendation", "cost_of_ignoring"],
                "additionalProperties": False,
            },
        },
        "happened": {"type": "array", "items": {"type": "string"}},
        "watching": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["needs_you", "happened", "watching"],
    "additionalProperties": False,
}


def load_projects() -> list[dict]:
    """Deliberately a tiny parser rather than a PyYAML dependency -- this file
    is three fields and never grows sideways."""
    if not PROJECTS_FILE.exists():
        return []
    projects, current = [], None
    for line in PROJECTS_FILE.read_text().splitlines():
        line = line.split("#")[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"\s*-\s+name:", line):
            if current:
                projects.append(current)
            current = {"name": line.split("name:", 1)[1].strip()}
        elif current is not None and ":" in line and line.startswith(("    ", "\t")):
            key, _, value = line.strip().partition(":")
            current[key.strip()] = value.strip()
    if current:
        projects.append(current)
    return projects


def recent(text: str, chars: int = 2500) -> str:
    return text[:chars] if text else "(empty)"


def main() -> None:
    today = dt.date.today().isoformat()
    projects = load_projects()
    if not projects:
        print("No projects configured.")
        return

    gathered = []
    for proj in projects:
        base = SCRIPT_DIR / proj.get("path", "project")
        gathered.append({
            "name": proj.get("name", base.name),
            "goal": recent((base / "goal.md").read_text() if (base / "goal.md").exists() else "", 1200),
            "open": recent((base / "open.md").read_text() if (base / "open.md").exists() else ""),
            "log": recent((base / "log.md").read_text() if (base / "log.md").exists() else ""),
        })

    client = anthropic.Anthropic(max_retries=0, timeout=300.0)
    resp = client.messages.create(
        model=MODEL, max_tokens=3000,
        output_config={"format": {"type": "json_schema", "schema": BRIEF_SCHEMA}},
        messages=[{"role": "user", "content": (
            "Write this morning's brief for the owner of these projects. It will be "
            "read aloud, so it must sound like a person talking: plain sentences, no "
            "jargon, no metrics dumps, no nested structure.\n\n"
            f"{json.dumps(gathered, indent=2)}\n\n"
            "needs_you: at most 3 items drawn from the open.md files, ranked by what "
            "actually matters most. Each needs the recommendation and what happens if "
            "it's ignored. If nothing genuinely needs him, return fewer or none -- do "
            "not manufacture urgency.\n"
            "happened: what the loops did yesterday, at most two lines per project. "
            "If a loop concluded 'not enough data', say so plainly; that is a real "
            "result, not a failure.\n"
            "watching: anything trending the wrong way but not yet worth a decision. "
            "Can be empty."
        )}],
    )
    brief = _structured_json(resp)

    lines = [f"# Morning brief — {today}", ""]
    if brief["needs_you"]:
        lines.append("## Needs you")
        lines.append("")
        for i, item in enumerate(brief["needs_you"][:3], 1):
            lines += [f"{i}. **{item['item']}** ({item['project']})", "",
                      f"   {item['recommendation']}", "",
                      f"   If you leave it: {item['cost_of_ignoring']}", ""]
    else:
        lines += ["## Needs you", "", "Nothing today.", ""]

    lines += ["## Happened", ""] + [f"{h}\n" for h in brief["happened"]]
    if brief["watching"]:
        lines += ["## Watching", ""] + [f"{w}\n" for w in brief["watching"]]

    text = "\n".join(lines)
    BRIEFS_DIR.mkdir(exist_ok=True)
    path = BRIEFS_DIR / f"brief-{today}.md"
    path.write_text(text)
    print(text)

    if upload_to_drive(path, text):
        print(f"\nDelivered to Google Drive: {DRIVE_FOLDER}/{path.name}")
    else:
        print(f"\nNo Drive credentials -- brief committed to {path.relative_to(SCRIPT_DIR)} instead.")


def upload_to_drive(path: Path, text: str) -> bool:
    """Uploads via a service account if GOOGLE_SERVICE_ACCOUNT_JSON is set.
    Returns False (not an exception) when unconfigured, so a missing key
    degrades to the repo fallback rather than failing the morning brief."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    folder_id = os.environ.get("GDRIVE_BRIEFS_FOLDER_ID")
    if not raw or not folder_id:
        return False
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload

        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/drive.file"])
        service = build("drive", "v3", credentials=creds)
        service.files().create(
            body={"name": path.name, "parents": [folder_id]},
            media_body=MediaInMemoryUpload(text.encode(), mimetype="text/markdown"),
            fields="id",
        ).execute()
        return True
    except Exception as exc:
        print(f"(Drive upload failed: {type(exc).__name__}: {exc} -- falling back to repo)")
        return False


if __name__ == "__main__":
    main()

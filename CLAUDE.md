# CLAUDE.md

Read on every Actions run. Kept under a page on purpose — bloat here costs on
every run.

## What this is

An Instagram account reviewing AI tools and productivity software for general
productivity/AI enthusiasts, monetised through affiliate links. Posts are
generated, rendered and published daily with no human in the loop. Full
objective in `project/goal.md`.

Current state worth knowing: reach is ~2 per post and saves are 0. Distribution
is the bottleneck, not copy quality.

## project/playbook.md is an input, not a report

`generate_content.py` loads it and injects it into the generation prompt before
any post is written. Editing it changes tomorrow's post. Treat every directive
as an instruction that overrides generic best practice, and never let it grow
past ~20 — retire the weakest instead.

## Escalation rules

- Never publish, merge, or delete without Mikael's approval.
- Never change the publishing credentials or the publishing code path
  (`publish_to_buffer.py`, `AUTO_PUBLISH`, `HAS_AFFILIATE_LINKS`).
- Escalate if: the action is irreversible, it spends money, it publishes
  publicly under his name, or it conflicts with `goal.md`.
- Otherwise: proceed and log it.
- Max 3 escalations per day. More than that, rank and drop the rest.

Full list, including the API-spend limits, in `project/constraints.md`.

## Nothing publishes or merges on its own

The daily post publishes automatically — that is already agreed. Everything
else stops at Mikael. Loops open PRs and issues; they never merge, and they
never push to `main` except for the markdown files listed below.

## Where each loop writes

| Loop | Schedule (UTC) | Writes |
|---|---|---|
| `daily-post` | 05:17 | `posts/`, `history.json`, `project/posts.jsonl`, publishes to Buffer |
| `loop-stats` | 01:43 | `project/playbook.md`, `project/log.md` — markdown only, never code |
| `loop-critique` | 03:37 | GitHub issues labelled `critique`, `project/log.md` |
| `loop-build` | 04:29 | A branch and a PR. Never `main`, never a merge |
| `portfolio-brief` | 06:11 | `briefs/`, or Google Drive when configured |

`project/open.md` is what needs Mikael's attention; the brief reads it, so keep
it short and remove resolved items.

## Cost discipline

API spend has caused real problems: one run cost EUR 4 in silent retries. Do not
raise `max_uses` on web search, remove `max_retries=0`, or add model calls to the
daily path without asking first.

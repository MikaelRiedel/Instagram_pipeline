# Constraints

- Never publish, merge, or delete without human approval.
- Never change the publishing credentials or the publishing code path.
- Escalate if: the action is irreversible, it spends money, it publishes
  publicly under my name, or it conflicts with goal.md.
- Otherwise: proceed and log it.
- Maximum 3 escalations per day. If there are more, rank and drop the rest.

## Repo-specific

- `publish_to_buffer.py` and the Buffer credentials are the publishing path.
  Loops may read it; they may not modify it.
- `AUTO_PUBLISH = True` in `pipeline_common.py` means generated posts go live
  without review. Loops must not flip this, in either direction, without asking.
- API spend is real and has been a problem: one bad run cost EUR 4 in retries.
  Loops must not raise `max_uses` on web search, remove `max_retries=0`, or add
  model calls to the daily path without escalating first.
- The repo is public. Nothing written into `project/` may contain API keys,
  tokens, or anything else that should stay private.
- Claims about a tool's pricing, features, or adoption must trace to a source
  in the post's `sources` list. Loops may not introduce directives that
  encourage invented specifics or fabricated personal experience.

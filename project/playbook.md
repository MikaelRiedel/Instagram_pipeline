# Playbook

Accumulated beliefs about what works, expressed as directives. This file is
**an input to generation**, not a report: `generate_content.py` loads it and
puts it in the prompt before any post is written. Editing it changes tomorrow's
post.

Each directive carries a date and a one-line reason. The stats loop may add,
change, or retire directives; it may not exceed ~20, and must retire the
weakest rather than appending past that.

## Directives

- 2026-09-18 — Sell the transformation, not the spec sheet. Lead with what
  becomes possible or what disappears from the reader's day.
  *Reason: seeded from the carousel spec; earlier feature-list posts read like
  brochures.*

- 2026-09-18 — Never build a slide around pricing, tiers, or quotas. Mention
  cost at most once in the caption, only if it is genuinely remarkable.
  *Reason: seeded; a post with three pricing slides was judged unengaging.*

- 2026-09-18 — The hook slide gets the most effort. Max 12 words, must stand
  alone as a thumbnail, must make sense without slide 2.
  *Reason: seeded from the spec; the hook carries ~80% of whether anyone swipes.*

- 2026-09-18 — Be evocative and concrete at the same time. "It sits in your
  meetings and hands you the decisions afterwards" beats both "boosts
  productivity" and "1,200 transcription minutes".
  *Reason: seeded; vague copy and spec-sheet copy both tested badly by eye.*

- 2026-09-18 — Never claim personal experience with a tool. Write as an
  informed reviewer citing what users report.
  *Reason: seeded; an early post invented "I haven't taken a note in 90 days",
  which is an FTC endorsement problem on an affiliate account.*

- 2026-09-24 — Close the caption and the final slide with a concrete reason
  to send the post to one specific person ("send this to whoever still types
  out every email") or a question to answer in the comments -- not a
  save-only CTA. A save mention can still appear, but pair it with a
  send/comment prompt.
  *Reason: filed as issue #6 by the critique loop; reach is the current
  gating metric, and shares/sends plus comments are what the test-pool
  mechanism weighs in the first ~90 minutes, while saves only matter once
  reach already exists.*

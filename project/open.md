# Open — needs Mikael

Short and current. The portfolio brief reads this file. Items are removed once
resolved.

## 1. Nobody is seeing the posts

Three published posts reached 1, 2 and 2 accounts, with 0 saves. The content
pipeline works; distribution does not exist. Every downstream goal (saves,
clicks, affiliate signups) is gated on this.

**Recommendation:** treat reach as the objective for the next few weeks, not
copy quality. Concretely: add hashtags that a zero-follower account can
actually rank in, and test Reels, which reach 3-5x further than carousels for
small accounts.

**If ignored:** the stats loop has nothing to learn from, the critique loop
optimises copy nobody reads, and the account stays at reach ~2 indefinitely.

**Waiting on you (Reels half of this):** `scripts/make_reel.py` now renders any
existing post's slides into a vertical MP4 with no editing —
`python3 scripts/make_reel.py` (needs ffmpeg) and watch the result. Actually
posting one needs two things a loop may not decide: a one-time edit to
`publish_to_buffer.py` so it can send video, and a call on audio (silent, or a
licensed track, or added by hand in the app — Instagram's own library only
works in-app).

## 2. The bio link goes nowhere

Every post ends with "link in bio". There is no bio link.

**Recommendation:** a free Linktree page listing the tools covered so far, even
with plain non-affiliate links for now. ~15 minutes.

**If ignored:** every post drives traffic to a dead end, and the CTA on all
future posts is wasted.

## 3. No affiliate programs joined

`HAS_AFFILIATE_LINKS = False`, so posts correctly avoid claiming an affiliate
relationship. There is also therefore no revenue path.

**Recommendation:** pick 2-3 programs for tools already covered (Krisp, Wispr
Flow, Otter) and apply.

**If ignored:** the account can grow but cannot earn.

## 4. Scheduled runs fire hours late

Cron is set to 05:17 UTC; recent runs started 09:44 and 09:58 UTC. Still ahead
of the 14:00 Finnish posting slot, but the margin is being eaten, and three
more daily workflows are about to be added.

**Recommendation:** none yet — watching. Acceptable while runs still land
before the slot.

**If ignored:** a post occasionally misses its day and publishes the next.

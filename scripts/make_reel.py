"""
Reel renderer -- turns an already-rendered post's slide PNGs into one vertical
MP4: slow Ken Burns zoom over each slide, crossfades between them.

This is step one of issue #2 (carousels reach non-followers badly; Reels are
the format that does). Deliberately standalone:

  - it is NOT wired into generate_content.py, so the daily post is unchanged;
  - it does NOT touch publish_to_buffer.py, which would need a human-approved
    edit before Buffer could accept video at all.

Both of those are Mikael's calls. What this script settles first is the only
question a loop can settle on its own: does the automated render actually
produce a Reel worth publishing?

No model calls, no network, no API spend -- it reads PNGs that already exist
on disk and shells out to ffmpeg. Stdlib only, on purpose: the whole point is
that this can be checked out and run without touching the pipeline's deps.

    python3 scripts/make_reel.py                    # newest post folder
    python3 scripts/make_reel.py --date 2026-09-21
    python3 scripts/make_reel.py --dry-run          # print the ffmpeg command

Needs ffmpeg >= 4.3 on PATH (the xfade filter landed in 4.3).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Not imported from pipeline_common on purpose: that module pulls in Pillow,
# requests and imagery, and this script needs none of them.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
POSTS_DIR = SCRIPT_DIR / "posts"

REEL_W, REEL_H = 1080, 1920   # Instagram vertical 9:16
FPS = 30

# The slides are 4:5 (1080x1350). Rather than cropping them to 9:16 -- which
# would eat the text -- each slide is laid on a blurred, darkened copy of
# itself at 90% width. The zoom then has margin to work in and never reaches
# the text.
CARD_W = 972                  # 90% of REEL_W
BLUR_W, BLUR_H = 270, 480     # backdrop is blurred at this size, then upscaled
BLUR_SIGMA = 8

SECONDS_PER_SLIDE = 3.0
CROSSFADE = 0.5
ZOOM_RANGE = 0.08             # 8% over the length of a slide; slower reads as broken

# zoompan is jittery when it zooms a frame at its own resolution, because the
# crop window is computed in whole input pixels. Supersampling first makes the
# steps invisible.
SUPERSAMPLE = 2

# Instagram plays longer Reels, but past this the drop-off is brutal and the
# upload paths get fussier. Warn rather than refuse -- it's a valid choice.
LONG_REEL_SECONDS = 90.0


def find_post_dir(requested_date: str | None) -> Path:
    if requested_date:
        post_dir = POSTS_DIR / requested_date
        if not post_dir.exists():
            raise SystemExit(f"No folder found at {post_dir}")
        return post_dir
    candidates = sorted(p for p in POSTS_DIR.iterdir() if p.is_dir())
    if not candidates:
        raise SystemExit(f"No post folders found in {POSTS_DIR}.")
    return candidates[-1]


def find_slides(post_dir: Path) -> list[Path]:
    slides = sorted(post_dir.glob("slide_*.png"))
    if not slides:
        raise SystemExit(
            f"No slide_*.png in {post_dir}. Render the carousel first "
            "(generate_content.py, or revise_content.py on an existing post)."
        )
    return slides


def _zoom_expression(index: int, frames: int) -> str:
    """Alternating slow zoom in / zoom out, so consecutive slides don't all
    drift the same way. Driven by the output frame number rather than by
    accumulating onto the previous zoom, which drifts and stutters."""
    step = ZOOM_RANGE / (frames - 1)
    if index % 2 == 0:
        return f"1+{step:.8f}*on"
    return f"{1 + ZOOM_RANGE:.8f}-{step:.8f}*on"


def _slide_chain(index: int, frames: int) -> str:
    """Filter chain for one slide: blurred 9:16 backdrop, the slide centred on
    top of it, then the zoom over the composed frame.

    Composed at SUPERSAMPLE scale and zoomed back down to 1080x1920, so the
    slide is only ever resampled once and zoompan has real pixels to crop.
    The backdrop is blurred at thumbnail size and scaled up -- a sigma-32
    gaussian over a full 9:16 frame, ten times over, is most of the render
    cost and looks no different.
    """
    zoom = _zoom_expression(index, frames)
    big_w, big_h = REEL_W * SUPERSAMPLE, REEL_H * SUPERSAMPLE
    return (
        f"[{index}:v]split=2[bg{index}][fg{index}];"
        f"[bg{index}]scale={BLUR_W}:{BLUR_H}:force_original_aspect_ratio=increase,"
        f"crop={BLUR_W}:{BLUR_H},gblur=sigma={BLUR_SIGMA},"
        f"eq=brightness=-0.22:saturation=0.55,scale={big_w}:{big_h}[bgd{index}];"
        f"[fg{index}]scale={CARD_W * SUPERSAMPLE}:-2[fgs{index}];"
        f"[bgd{index}][fgs{index}]overlay=(W-w)/2:(H-h)/2[card{index}];"
        f"[card{index}]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={REEL_W}x{REEL_H}:fps={FPS},setsar=1,format=yuv420p[v{index}]"
    )


def build_filtergraph(count: int, seconds: float, crossfade: float) -> tuple[str, str, float]:
    """Returns (filter_complex, label of the finished video, total duration).

    Each xfade overlaps its two inputs by `crossfade`, so the timeline is
    shorter than count*seconds and every offset has to be tracked rather than
    guessed -- an offset that lands past the end of a stream silently freezes
    the last frame instead of erroring.
    """
    frames = round(seconds * FPS)
    if frames < 2:
        raise SystemExit(f"--seconds-per-slide {seconds} is too short at {FPS}fps.")
    if crossfade >= seconds:
        raise SystemExit(
            f"--crossfade {crossfade} must be shorter than --seconds-per-slide {seconds}."
        )

    parts = [_slide_chain(i, frames) for i in range(count)]

    last = "v0"
    total = seconds
    for i in range(1, count):
        offset = total - crossfade
        label = f"x{i}"
        parts.append(
            f"[{last}][v{i}]xfade=transition=fade:"
            f"duration={crossfade}:offset={offset:.3f}[{label}]"
        )
        total += seconds - crossfade
        last = label

    return ";".join(parts), last, total


def build_command(
    slides: list[Path],
    out_path: Path,
    seconds: float = SECONDS_PER_SLIDE,
    crossfade: float = CROSSFADE,
    audio: Path | None = None,
) -> tuple[list[str], float]:
    graph, video_label, total = build_filtergraph(len(slides), seconds, crossfade)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    for slide in slides:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds}", "-i", str(slide)]

    audio_index = len(slides)
    if audio is None:
        # A Reel with no audio stream at all is treated as a silent video by
        # some upload paths and rejected outright by others. Cheap insurance.
        cmd += [
            "-f", "lavfi", "-t", f"{total:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        audio_map = f"{audio_index}:a"
    else:
        cmd += ["-stream_loop", "-1", "-i", str(audio)]
        fade_at = max(total - 1.5, 0.0)
        graph += (
            f";[{audio_index}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={fade_at:.3f}:d=1.5[aout]"
        )
        audio_map = "[aout]"

    cmd += [
        "-filter_complex", graph,
        "-map", f"[{video_label}]",
        "-map", audio_map,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        "-t", f"{total:.3f}",
        str(out_path),
    ]
    return cmd, total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", help="Post folder under posts/ (default: the newest)")
    parser.add_argument("--out", help="Output file (default: <post folder>/reel.mp4)")
    parser.add_argument("--seconds-per-slide", type=float, default=SECONDS_PER_SLIDE)
    parser.add_argument("--crossfade", type=float, default=CROSSFADE)
    parser.add_argument(
        "--audio",
        help="Optional music track to mux in. Nothing ships with the repo -- "
             "licensing is a human decision, and Instagram's own audio library "
             "can only be applied in-app.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the ffmpeg command and exit. Works without ffmpeg installed.",
    )
    args = parser.parse_args()

    post_dir = find_post_dir(args.date)
    slides = find_slides(post_dir)
    out_path = Path(args.out) if args.out else post_dir / "reel.mp4"

    audio = None
    if args.audio:
        audio = Path(args.audio)
        if not audio.exists():
            raise SystemExit(f"No audio file at {audio}")

    cmd, total = build_command(
        slides, out_path,
        seconds=args.seconds_per_slide,
        crossfade=args.crossfade,
        audio=audio,
    )

    print(f"{len(slides)} slides from {post_dir.name} -> {total:.1f}s of video")
    if total > LONG_REEL_SECONDS:
        print(
            f"  /!\\ {total:.0f}s is long for a Reel. Lower --seconds-per-slide, "
            "or cut slides, if retention matters more than completeness."
        )
    if audio is None:
        print("  (silent -- pass --audio, or add a track in the Instagram app)")

    if args.dry_run:
        print("\n" + " ".join(cmd))
        return

    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg is not on PATH. Install it (apt-get install ffmpeg / brew "
            "install ffmpeg), or run with --dry-run to see the command."
        )

    print("Rendering (a ~30s Reel takes a minute or two)...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed with exit code {result.returncode}.")

    size_mb = out_path.stat().st_size / 1_000_000
    try:
        shown = out_path.relative_to(SCRIPT_DIR)
    except ValueError:
        shown = out_path
    print(f"\nWrote {shown}  ({size_mb:.1f} MB, {REEL_W}x{REEL_H}, {total:.1f}s)")
    print("Watch it before anything else happens to it. Nothing was uploaded,")
    print("published or committed -- the daily pipeline still renders carousels.")


if __name__ == "__main__":
    main()

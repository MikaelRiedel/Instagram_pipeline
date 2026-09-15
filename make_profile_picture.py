"""
Generates profile picture options for the Instagram account, using the same
palette and typeface as the carousel slides so the avatar and the grid read
as one brand.

Instagram crops avatars to a circle and shows them as small as ~32px in
feed, so every option here is built as a bold, high-contrast silhouette
inside a circular safe area -- fine detail would disappear.

Run:
    python3 make_profile_picture.py

Writes options into assets/profile/, plus preview_sheet.png showing each one
circle-cropped at real display size.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from pipeline_common import ACCENT_COLOR, DARK_BG, DARK_HEADING, _load_font

SIZE = 1080
OUT_DIR = Path(__file__).resolve().parent / "assets" / "profile"

# Change this and re-run if you want different initials on the monogram
# options -- e.g. your handle's first letters.
MONOGRAM = "AI"


def _sparkle_points(cx: float, cy: float, outer: float, inner_ratio: float = 0.30) -> list[tuple[float, float]]:
    """Four-pointed 'spark' polygon: long tips at the compass points, short
    inner points between them."""
    inner = outer * inner_ratio
    pts = []
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        r = outer if i % 2 == 0 else inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def _circle_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def sparkle_on_dark() -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), DARK_BG)
    draw = ImageDraw.Draw(img)
    c = SIZE / 2
    draw.polygon(_sparkle_points(c, c, SIZE * 0.31), fill=ACCENT_COLOR)
    # Small companion spark, offset -- reads as "AI" without a literal icon.
    draw.polygon(_sparkle_points(SIZE * 0.72, SIZE * 0.30, SIZE * 0.085), fill=DARK_HEADING)
    return img


def sparkle_on_accent() -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), ACCENT_COLOR)
    draw = ImageDraw.Draw(img)
    c = SIZE / 2
    draw.polygon(_sparkle_points(c, c, SIZE * 0.31), fill=DARK_BG)
    draw.polygon(_sparkle_points(SIZE * 0.72, SIZE * 0.30, SIZE * 0.085), fill=DARK_HEADING)
    return img


def monogram_on_dark() -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), DARK_BG)
    draw = ImageDraw.Draw(img)
    font = _load_font(bold=True, size=int(SIZE * 0.40))
    box = draw.textbbox((0, 0), MONOGRAM, font=font)
    draw.text(
        ((SIZE - (box[2] - box[0])) / 2 - box[0], (SIZE - (box[3] - box[1])) / 2 - box[1]),
        MONOGRAM,
        font=font,
        fill=DARK_HEADING,
    )
    # Accent spark tucked top-right, echoing the carousel's accent bar.
    draw.polygon(_sparkle_points(SIZE * 0.76, SIZE * 0.26, SIZE * 0.10), fill=ACCENT_COLOR)
    return img


def monogram_on_accent() -> Image.Image:
    img = Image.new("RGB", (SIZE, SIZE), ACCENT_COLOR)
    draw = ImageDraw.Draw(img)
    font = _load_font(bold=True, size=int(SIZE * 0.40))
    box = draw.textbbox((0, 0), MONOGRAM, font=font)
    draw.text(
        ((SIZE - (box[2] - box[0])) / 2 - box[0], (SIZE - (box[3] - box[1])) / 2 - box[1]),
        MONOGRAM,
        font=font,
        fill=DARK_BG,
    )
    return img


def preview_sheet(options: dict[str, Image.Image]) -> Image.Image:
    """Shows each option circle-cropped at the sizes Instagram actually uses,
    which is the real test -- an avatar that only works at 1080px is useless."""
    sizes = [150, 64, 32]
    pad = 28
    row_h = max(sizes) + pad * 2
    label_font = _load_font(bold=False, size=22)
    width = pad + sum(s + pad * 2 for s in sizes) + 260
    sheet = Image.new("RGB", (width, row_h * len(options)), "#0C0C12")
    draw = ImageDraw.Draw(sheet)

    for row, (name, img) in enumerate(options.items()):
        y0 = row * row_h
        draw.text((pad, y0 + pad), name, font=label_font, fill="#9A9AB0")
        x = 260
        for s in sizes:
            thumb = img.resize((s, s), Image.LANCZOS)
            thumb.putalpha(_circle_mask(s))
            sheet.paste(thumb, (x, y0 + (row_h - s) // 2), thumb)
            x += s + pad * 2
    return sheet


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    options = {
        "sparkle-on-dark": sparkle_on_dark(),
        "sparkle-on-purple": sparkle_on_accent(),
        f"monogram-{MONOGRAM}-dark": monogram_on_dark(),
        f"monogram-{MONOGRAM}-purple": monogram_on_accent(),
    }
    for name, img in options.items():
        path = OUT_DIR / f"{name}.png"
        img.save(path)
        print(f"  wrote {path.relative_to(Path(__file__).resolve().parent)}")

    sheet_path = OUT_DIR / "preview_sheet.png"
    preview_sheet(options).save(sheet_path)
    print(f"  wrote {sheet_path.relative_to(Path(__file__).resolve().parent)}")
    print("\nOpen preview_sheet.png -- it shows each option circle-cropped at")
    print("150px, 64px and 32px, which is how Instagram actually displays it.")


if __name__ == "__main__":
    main()

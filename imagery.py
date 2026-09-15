"""
Sources the pictures that go on the slides.

Two kinds, deliberately:
  - PRODUCT shots: the tool's own og:image, which on virtually every SaaS
    site is a polished hero screenshot of the actual software. Free, no key,
    no headless browser -- one HTTP request.
  - ATMOSPHERIC photos: real photography for the emotional slides (hook,
    the status quo, the payoff) where a product screenshot would say nothing.
    Uses Pexels, which needs a free API key in PEXELS_API_KEY.

Everything here fails soft. A missing key, a dead site, a weird image format
-- all return None, and the renderer falls back to a graphic treatment. An
unattended daily job must never crash because someone's marketing page was
down.
"""

from __future__ import annotations

import os
import re
from io import BytesIO

import requests
from PIL import Image

TIMEOUT = 12

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _download(url: str) -> Image.Image | None:
    try:
        resp = requests.get(url, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        return img.convert("RGB")
    except Exception as exc:
        print(f"    (image fetch failed: {type(exc).__name__} for {url[:60]})")
        return None


def og_image(site_url: str) -> Image.Image | None:
    """The tool's own hero image -- usually a real screenshot of the product,
    already art-directed by the people who built it."""
    if not site_url:
        return None
    try:
        html = requests.get(
            site_url, headers={"User-Agent": BROWSER_UA}, timeout=TIMEOUT
        ).text
    except Exception as exc:
        print(f"    (couldn't load {site_url[:50]}: {type(exc).__name__})")
        return None

    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            url = match.group(1)
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("/"):
                base = re.match(r"https?://[^/]+", site_url)
                url = (base.group(0) if base else "") + url
            return _download(url)

    print(f"    (no og:image on {site_url[:50]})")
    return None


def stock_photo(query: str) -> Image.Image | None:
    """Atmospheric photography for the slides where a product shot says
    nothing. Returns None when PEXELS_API_KEY isn't set, which is a normal
    state -- the renderer draws a graphic instead."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key or not query:
        return None
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 1, "orientation": "portrait"},
            headers={"Authorization": key},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if not photos:
            print(f"    (no stock photo for '{query}')")
            return None
        return _download(photos[0]["src"]["large2x"])
    except Exception as exc:
        print(f"    (stock photo lookup failed: {type(exc).__name__})")
        return None


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale and centre-crop to exactly fill `size`, like CSS object-fit:
    cover -- so photos never appear stretched or letterboxed."""
    target_w, target_h = size
    scale = max(target_w / img.width, target_h / img.height)
    resized = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))

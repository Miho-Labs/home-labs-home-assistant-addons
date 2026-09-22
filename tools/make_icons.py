"""Render the Home Labs mark to every PNG the add-ons need.

Per add-on folder, from its icon.svg / logo.svg (the source of truth):
- icon.png (128x128), logo.png (250x100): the add-on store card.
- Home Labs Backup also ships its integration's brand images in
  custom_components/home_labs_backup/brand/, which Home Assistant (2026.3+)
  serves for custom integrations — the icon of the "Home Labs (chmura)"
  backup location. Sizes follow the brands repo (icon 256, @2x 512).

Headless Chromium through Playwright, a dev-only dependency:

    pip install playwright && playwright install chromium
    python3 tools/make_icons.py

Set CHROMIUM_EXECUTABLE to use an already installed Chromium instead.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = (("icon.svg", "icon.png", 128, 128), ("logo.svg", "logo.png", 250, 100))
BRAND = "custom_components/home_labs_backup/brand"
TARGETS = {
    "home_labs_sync": STORE,
    "home_labs_backup": (
        *STORE,
        ("icon.svg", f"{BRAND}/icon.png", 256, 256),
        ("icon.svg", f"{BRAND}/icon@2x.png", 512, 512),
        ("logo.svg", f"{BRAND}/logo.png", 320, 128),
        ("logo.svg", f"{BRAND}/logo@2x.png", 640, 256),
    ),
}


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    return struct.unpack(">II", head[16:24])


def render(svg: Path, png: Path, width: int, height: int) -> None:
    from playwright.sync_api import sync_playwright

    html = (
        "<!doctype html><html><body style='margin:0;background:transparent'>"
        f"<div id='box' style='width:{width}px;height:{height}px'>"
        + svg.read_text(encoding="utf-8").replace(
            "<svg ", f"<svg style='display:block;width:{width}px;height:{height}px' ", 1
        )
        + "</div></body></html>"
    )
    with sync_playwright() as p:
        executable = os.environ.get("CHROMIUM_EXECUTABLE") or None
        browser = p.chromium.launch(executable_path=executable)
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.set_content(html)
        page.locator("#box").screenshot(path=str(png), omit_background=True, type="png")
        browser.close()


def main() -> int:
    for addon, targets in TARGETS.items():
        for svg_name, png_name, width, height in targets:
            svg, png = ROOT / addon / svg_name, ROOT / addon / png_name
            png.parent.mkdir(parents=True, exist_ok=True)
            render(svg, png, width, height)
            got = _png_size(png)
            if got != (width, height):
                print(f"{addon}/{png_name}: expected {width}x{height}, got {got}", file=sys.stderr)
                return 1
            print(f"{addon}/{png_name}: {got[0]}x{got[1]} from {svg_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

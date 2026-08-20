"""One-off generator for PWA icons (U24.12). Run manually, output is committed:

    ./venv/bin/python scripts/generate_pwa_icons.py

Draws the "LR" mark on a slate-900 rounded square:
  - icon-192.png / icon-512.png (any + maskable purpose candidates)
  - icon-maskable-192.png / icon-maskable-512.png (80% safe-zone padding)
  - apple-touch-icon.png (180px, no transparency for iOS)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("app/static/img/icons")
FONT_PATH = "/tmp/opencode/Manrope.ttf"
BG = (15, 23, 42, 255)  # slate-900 / theme #0f172a
FG = (255, 255, 255, 255)


def draw_icon(size: int, corner_ratio: float, mark_ratio: float, weight: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = int(size * corner_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BG)
    font = ImageFont.truetype(FONT_PATH, int(size * mark_ratio))
    font.set_variation_by_axes([weight])
    bbox = d.textbbox((0, 0), "LR", font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), "LR", font=font, fill=FG)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Regular icons: bigger radius, mark fills most of the canvas.
    for size in (192, 512):
        draw_icon(size, corner_ratio=0.22, mark_ratio=0.44, weight=700).save(
            OUT / f"icon-{size}.png"
        )
    # Maskable: content inside 80% safe zone => draw mark smaller on full-bleed square.
    for size in (192, 512):
        img = Image.new("RGBA", (size, size), BG)
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(FONT_PATH, int(size * 0.34))
        font.set_variation_by_axes([700])
        bbox = d.textbbox((0, 0), "LR", font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), "LR", font=font, fill=FG)
        img.save(OUT / f"icon-maskable-{size}.png")
    # Apple touch: 180px, opaque background (iOS dislikes transparency).
    apple = draw_icon(180, corner_ratio=0.0, mark_ratio=0.44, weight=700)
    solid = Image.new("RGB", apple.size, BG[:3])
    solid.paste(apple, (0, 0), apple)
    solid.save(OUT / "apple-touch-icon.png")
    print("wrote icons to", OUT)


if __name__ == "__main__":
    main()

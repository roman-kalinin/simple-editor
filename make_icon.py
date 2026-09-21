"""Generate the app icon (app.ico) and favicon (favicon.png).

Run once (or whenever you tweak the design): python make_icon.py
Requires Pillow (pip install pillow).
"""
from PIL import Image, ImageDraw


def make(size):
    S = size * 8  # supersample for smooth edges, then downscale
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(S * 0.22)
    # Rounded-square background with the app's blue accent border.
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=(30, 34, 42, 255))
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r,
                        outline=(59, 125, 216, 255), width=max(1, S // 64))
    # Filmstrip perforations top & bottom.
    hole_w = int(S * 0.07)
    hole_h = int(S * 0.05)
    gap = int(S * 0.06)
    y1 = int(S * 0.10)
    y2 = int(S * 0.85)
    x = int(S * 0.13)
    while x < S - int(S * 0.13):
        d.rounded_rectangle([x, y1, x + hole_w, y1 + hole_h], radius=hole_w // 3, fill=(70, 78, 92, 255))
        d.rounded_rectangle([x, y2, x + hole_w, y2 + hole_h], radius=hole_w // 3, fill=(70, 78, 92, 255))
        x += hole_w + gap
    # Centered play triangle.
    cx, cy = S * 0.5, S * 0.5
    tw = S * 0.26
    pts = [(cx - tw * 0.55, cy - tw * 0.75),
           (cx - tw * 0.55, cy + tw * 0.75),
           (cx + tw * 0.85, cy)]
    d.polygon(pts, fill=(59, 125, 216, 255))
    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [make(s) for s in sizes]
    imgs[0].save("app.ico", sizes=[(s, s) for s in sizes], append_images=imgs[1:])
    make(256).save("favicon.png")
    print("wrote app.ico and favicon.png")


if __name__ == "__main__":
    main()

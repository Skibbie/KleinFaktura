"""Erzeugt das Programmsymbol: Rechnungsblatt mit Textzeilen auf Grün.

Aufruf: python tools/make_icon.py  ->  assets/icon.png, assets/icon.ico
"""
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets"
S = 1024  # Arbeitsgröße (wird herunterskaliert -> glatte Kanten)

GREEN = (23, 96, 74)
SHEET = (255, 255, 255)
FOLD = (205, 222, 215)
INK = (29, 43, 47)


def draw(size_hint: int) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((32, 32, S - 32, S - 32), radius=200, fill=GREEN)
    # Blatt mit Eselsohr
    x0, y0, x1, y1, fold = 250, 150, 774, 874, 170
    d.polygon([(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)], fill=SHEET)
    d.polygon([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], fill=FOLD)
    # Textzeilen (bei kleinen Größen dicker, weniger Zeilen)
    small = size_hint <= 32
    line_h = 70 if small else 44
    gap = 60 if small else 50
    top = 330 if small else 340
    lengths = [420, 330, 420] if small else [420, 330, 420, 260, 380]
    left = x0 + 52
    for i, ln in enumerate(lengths):
        yt = top + i * (line_h + gap)
        d.rounded_rectangle((left, yt, left + ln, yt + line_h), radius=line_h // 2, fill=INK)
    return img


def main():
    ASSETS.mkdir(exist_ok=True)
    big = draw(256).resize((256, 256), Image.LANCZOS)
    big.save(ASSETS / "icon.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = [draw(s).resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(ASSETS / "icon.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    print("Symbol erstellt:", ASSETS / "icon.ico")


if __name__ == "__main__":
    main()

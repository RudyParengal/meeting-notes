"""Draws desktop/notewell.ico: a blue note with three lines. Run once after changing it.

    python desktop/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((16, 16, S - 16, S - 16), radius=48, fill=(59, 91, 219))   # dashboard --accent
for i, width in enumerate((150, 150, 96)):
    y = 78 + i * 46
    d.rounded_rectangle((56, y, 56 + width, y + 22), radius=11, fill=(255, 255, 255))
img.save(Path(__file__).with_name("notewell.ico"),
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

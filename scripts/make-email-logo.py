#!/usr/bin/env python3
"""
Generate EightFold email signature logo — lemniscate only.
Output: images/eightfold-logo.png
"""
from PIL import Image, ImageDraw
import math, os

INK = (26, 26, 26)  # #1a1a1a — dark, for light email backgrounds

# Render at 2x for retina crispness
W, H = 360, 200
img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

def draw_lemniscate(cx, cy, scale, stroke, color):
    pts = []
    n = 960
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        denom = 1 + math.sin(t) ** 2
        x = scale * math.cos(t) / denom
        y = scale * math.sin(t) * math.cos(t) / denom
        pts.append((cx + x, cy + y))
    pts.append(pts[0])
    draw.line(pts, fill=color + (255,), width=stroke, joint='curve')

draw_lemniscate(W // 2, H // 2, scale=130, stroke=14, color=INK)

bbox = img.getbbox()
if bbox:
    img = img.crop(bbox)

pad = 10
padded = Image.new('RGBA', (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
padded.paste(img, (pad, pad))

out_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'images', 'eightfold-logo.png'))
padded.save(out_path, 'PNG', optimize=True)
print(f'wrote {out_path}  {os.path.getsize(out_path):,} bytes  {padded.size}')

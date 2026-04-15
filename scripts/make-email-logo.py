#!/usr/bin/env python3
"""
Generate EightFold email signature logo — lime lemniscate matching the website.
Draws the exact cubic Bezier path from index.html using PIL.
Output: images/eightfold-logo.png
"""
from PIL import Image, ImageDraw
import os

# SVG path: M50 30 C65 10 95 10 95 30 C95 50 65 50 50 30 C35 10 5 10 5 30 C5 50 35 50 50 30Z
# Four cubic Bezier segments in a 100x60 viewBox.
CURVES = [
    ((50,30), (65,10), (95,10), (95,30)),
    ((95,30), (95,50), (65,50), (50,30)),
    ((50,30), (35,10), ( 5,10), ( 5,30)),
    (( 5,30), ( 5,50), (35,50), (50,30)),
]

ACCENT = (200, 255, 0)  # #c8ff00 lime

def cubic_bezier(p0, p1, p2, p3, steps=120):
    """Sample a cubic Bezier curve."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3*p0[0] + 3*u**2*t*p1[0] + 3*u*t**2*p2[0] + t**3*p3[0]
        y = u**3*p0[1] + 3*u**2*t*p1[1] + 3*u*t**2*p2[1] + t**3*p3[1]
        pts.append((x, y))
    return pts

# Build full path in viewBox coordinates (100x60)
path = []
for curve in CURVES:
    path.extend(cubic_bezier(*curve))

# Scale up for a crisp render — target ~400px wide
SCALE = 4.5
W_VB, H_VB = 100, 60
W = int(W_VB * SCALE)
H = int(H_VB * SCALE)

scaled = [(x * SCALE, y * SCALE) for x, y in path]

img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Subtle fill
draw.polygon(scaled, fill=ACCENT + (15,))

# Lime stroke
draw.line(scaled + [scaled[0]], fill=ACCENT + (255,), width=int(3.5 * SCALE), joint='curve')

# Crop to content + padding
bbox = img.getbbox()
if bbox:
    img = img.crop(bbox)

pad = 12
out = Image.new('RGBA', (img.width + pad*2, img.height + pad*2), (0, 0, 0, 0))
out.paste(img, (pad, pad))

out_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'images', 'eightfold-logo.png'))
out.save(out_path, 'PNG', optimize=True)
print(f'wrote {out_path}  {os.path.getsize(out_path):,} bytes  {out.size}')

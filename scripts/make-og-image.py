#!/usr/bin/env python3
"""
Generate the EightFold Open Graph image (1200x630).
Re-run with: python3 scripts/make-og-image.py
Uses PIL + macOS system fonts; no extra dependencies.
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math, random, os

W, H = 1200, 630

VOID    = (10, 10, 10)
RICE    = (244, 236, 224)
INK     = (213, 203, 186)
DIM     = (128, 117, 103)
ACCENT  = (200, 255, 0)     # lime
STAMP   = (192, 58, 43)     # cinnabar

def load(path, size, idx=0):
    try:    return ImageFont.truetype(path, size, index=idx)
    except: return ImageFont.load_default()

DIDOT_PATH  = '/System/Library/Fonts/Supplemental/Didot.ttc'
SONGTI_PATH = '/System/Library/Fonts/Supplemental/Songti.ttc'
# For eyebrow/tagline — plain latin mono for the meta line (no CJK in it,
# so Menlo is fine). Chinese goes in separate Songti runs.
MONO_PATH   = '/System/Library/Fonts/Menlo.ttc'

didot_bold   = load(DIDOT_PATH,  156, idx=1)
didot_italic = load(DIDOT_PATH,  32,  idx=2)
songti_bold  = load(SONGTI_PATH, 58,  idx=2)
songti_med   = load(SONGTI_PATH, 26,  idx=1)
stamp_glyph  = load(SONGTI_PATH, 76,  idx=2)
mono_sm      = load(MONO_PATH,   20,  idx=0)

# ─────────────────────────── Canvas + atmosphere ───────────────────────────
img = Image.new('RGB', (W, H), VOID)
draw = ImageDraw.Draw(img, 'RGBA')

def radial(cx, cy, radius, color, alpha_peak=28, blur=30):
    g = Image.new('RGBA', (W, H), (0,0,0,0))
    gd = ImageDraw.Draw(g)
    steps = 50
    for i in range(steps, 0, -1):
        r = int(radius * i / steps)
        a = int(alpha_peak * (1 - i / steps))
        gd.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color + (a,))
    return g.filter(ImageFilter.GaussianBlur(blur))

img.alpha_composite = None  # PIL RGB paste path
for glow in (
    radial(200, 160, 540, ACCENT, 30),
    radial(1060, 540, 420, STAMP, 22),
    radial(900, 120, 260, ACCENT, 14),
):
    img.paste(glow, (0,0), glow)

# Sparse dust
random.seed(8)
dust = Image.new('RGBA', (W, H), (0,0,0,0))
dd = ImageDraw.Draw(dust)
for _ in range(240):
    x, y = random.randint(0, W), random.randint(0, H)
    dd.ellipse([x, y, x+1, y+1], fill=(244,236,224, random.randint(6, 18)))
img.paste(dust, (0,0), dust)

# Hairline frame — editorial border echo
draw.rectangle([40, 40, W-40, H-40], outline=(42,39,35,255), width=1)

# ─────────────────────────── Infinity logo (parametric lemniscate) ───────────────────────────
def draw_lemniscate(cx, cy, scale, stroke, color):
    """Bernoulli lemniscate: r² = a² cos(2θ). Sampled densely and drawn as connected segments."""
    a = scale
    pts = []
    n = 480
    for i in range(n + 1):
        # t spans 0..2π but lemniscate uses param form via θ with branches
        t = -math.pi/2 + (math.pi * i / n)
        # Parametric form that traces a clean figure-8
        denom = 1 + math.sin(t)**2
        x = a * math.cos(t) / denom
        y = a * math.sin(t) * math.cos(t) / denom
        pts.append((cx + x, cy + y))
    # Close the loop
    pts.append(pts[0])
    # Draw as thick line
    draw.line(pts, fill=color + (255,), width=stroke, joint='curve')

# Small logo, top-left, echoing the site's hero
draw_lemniscate(135, 130, scale=62, stroke=8, color=ACCENT)

# ─────────────────────────── Wordmark block ───────────────────────────
TEXT_X = 90

# Mono eyebrow above the wordmark
eyebrow_y = 230
draw.text((TEXT_X, eyebrow_y), 'MULTILINGUAL  ·  BRAND  STUDIO',
          font=mono_sm, fill=DIM + (255,))
# Accent tick before eyebrow
draw.line([(TEXT_X - 22, eyebrow_y + 10), (TEXT_X - 6, eyebrow_y + 10)],
          fill=ACCENT + (255,), width=2)

# Primary wordmark — Didot bold
draw.text((TEXT_X - 6, 262), 'EightFold', font=didot_bold, fill=RICE + (255,))

# Chinese wordmark — stacked BELOW the Latin for clean hierarchy
# Slight left indent so the Songti optically aligns under 'E'
draw.text((TEXT_X, 420), '八重 — 八种专业的折叠',
          font=songti_bold, fill=(213, 203, 186, 200))

# Hairline divider between wordmark stack and tagline
draw.line([(TEXT_X, 505), (TEXT_X + 70, 505)],
          fill=ACCENT + (255,), width=2)

# Italic sub-tagline
draw.text((TEXT_X, 520),
          'Chinese brands, fluent across Europe.',
          font=didot_italic, fill=INK + (255,))

# ─────────────────────────── Cinnabar chop (top-right) ───────────────────────────
CHOP = 124
cx0 = W - CHOP - 90
cy0 = 90
draw.rounded_rectangle(
    [cx0, cy0, cx0 + CHOP, cy0 + CHOP],
    radius=6, outline=STAMP + (255,), width=4
)
# Inner subtle fill
draw.rounded_rectangle(
    [cx0+8, cy0+8, cx0 + CHOP - 8, cy0 + CHOP - 8],
    radius=3, outline=STAMP + (80,), width=1
)
bbox = draw.textbbox((0,0), '重', font=stamp_glyph)
gw, gh = bbox[2]-bbox[0], bbox[3]-bbox[1]
draw.text(
    (cx0 + (CHOP - gw) // 2 - bbox[0],
     cy0 + (CHOP - gh) // 2 - bbox[1] - 2),
    '重', font=stamp_glyph, fill=STAMP + (255,)
)

# ─────────────────────────── Meta strip bottom-right ───────────────────────────
meta = 'LISBON  ↔  SHANGHAI   ·   EIGHTFOLD.WORK'
bbox = draw.textbbox((0,0), meta, font=mono_sm)
mw = bbox[2] - bbox[0]
draw.text((W - mw - 90, H - 80), meta, font=mono_sm, fill=DIM + (255,))
# Accent dot at the start of meta
draw.ellipse([W - mw - 104, H - 72, W - mw - 98, H - 66], fill=ACCENT + (255,))

# ─────────────────────────── Grain finish ───────────────────────────
grain = Image.new('RGBA', (W, H), (0,0,0,0))
gg = ImageDraw.Draw(grain)
for _ in range(3200):
    x, y = random.randint(0, W-1), random.randint(0, H-1)
    v = random.randint(0, 255)
    gg.point((x, y), fill=(v, v, v, random.randint(2, 7)))
img.paste(grain, (0,0), grain)

out_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'og-image.png'))
img.save(out_path, 'PNG', optimize=True)
print(f'wrote {out_path}  {os.path.getsize(out_path):,} bytes  {img.size}')

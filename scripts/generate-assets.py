"""Deterministic generated assets. The placeholders need no external graphics libraries; the font
bake rasterizes the committed OFL font with Pillow (FreeType), so only rerunning it needs Pillow."""
from pathlib import Path
import json
import math
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'games/tapdemo/assets'
# Fredoka, from google/fonts ofl/fredoka at 7085eb89a950e85db5b166b7a58d414544b4140c, under OFL.txt.
FONT = ASSETS / 'fonts/Fredoka.ttf'
FONT_WEIGHT = 600  # SemiBold, of the variable font's 300..700
# Pixel sizes baked: the renderer draws from the smallest at least as large as the text on screen,
# so no sheet is shrunk by more than 1.5x (bilinear filtering alone aliases past about 2x).
FONT_SIZES = (16, 24, 36, 54, 80, 120)
GLYPH_PAD = 2  # transparent texels around each glyph, so filtering never reads a neighbour

def png(path, size):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            dx, dy = x - size / 2, y - size / 2
            color = (70, 235, 196) if dx * dx + dy * dy < (size * .29) ** 2 else (9, 20, 33)
            if (x - size*.43)**2 + (y - size*.41)**2 < (size*.065)**2:
                color = (231, 255, 248)
            rows.extend((*color, 255))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b''))

def bmp32(path, width, height, pixel):
    """A 32-bit BMP with straight (unpremultiplied) alpha; pixel(x, y) -> (r, g, b, a), y down."""
    pixels = bytearray()
    for y in reversed(range(height)):
        for x in range(width):
            r, g, b, a = pixel(x, y)
            pixels.extend((b, g, r, a) if a else (0, 0, 0, 0))  # exporters leave clear pixels black
    # BITMAPV4HEADER with bit fields, which carries the alpha mask SDL_LoadBMP reads.
    dib = struct.pack('<IiiHHIIiiII', 108, width, height, 1, 32, 3, len(pixels), 2835, 2835, 0, 0)
    dib += struct.pack('<IIII', 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    dib += struct.pack('<I', 0x73524742) + bytes(36) + bytes(12)  # 'sRGB', unused endpoints and gamma
    path.write_bytes(b'BM' + struct.pack('<IHHI', 14 + len(dib) + len(pixels), 0, 0, 14 + len(dib)) + dib + pixels)

def bmp8(path, width, height, values):
    """An 8-bit greyscale BMP, row-major and top row first: the engine reads each value as coverage."""
    stride = (width + 3) & ~3
    pixels = bytearray()
    for y in reversed(range(height)):
        pixels.extend(values[y * width:(y + 1) * width])
        pixels.extend(bytes(stride - width))
    palette = b''.join(bytes((i, i, i, 0)) for i in range(256))
    dib = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 8, 0, len(pixels), 2835, 2835, 256, 0)
    offset = 14 + len(dib) + len(palette)
    path.write_bytes(b'BM' + struct.pack('<IHHI', offset + len(pixels), 0, 0, offset) + dib + palette + pixels)

def coverage(inside, x, y, samples=4):
    """The fraction of pixel (x, y) that inside(px, py) covers, by a samples x samples grid."""
    hits = sum(inside(x + (i + .5) / samples, y + (j + .5) / samples) for i in range(samples) for j in range(samples))
    return hits / (samples * samples)

def flower(x, y):
    """Render test cutout: a five-petal flower with an anti-aliased rim and a soft shadow halo."""
    c = 64
    def petals(px, py):
        dx, dy = px - c, py - c
        r, a = math.hypot(dx, dy), math.atan2(dy, dx)
        return r < 30 + 22 * abs(math.cos(2.5 * a))
    body = coverage(petals, x, y)
    halo = 0.0
    if body < 1:  # a shadow that fades out over 8 pixels: the soft edge
        dx, dy = x + .5 - c, y + .5 - c
        r, a = math.hypot(dx, dy), math.atan2(dy, dx)
        rim = 30 + 22 * abs(math.cos(2.5 * a))
        halo = max(0.0, 1 - max(0.0, r - rim) / 8) * 0.45
    d = math.hypot(x + .5 - c, y + .5 - c)
    centre = coverage(lambda px, py: math.hypot(px - c, py - c) < 16, x, y)
    petal = (255, 248, 236) if d > 20 else (255, 214, 92)
    rgb = tuple(round(p * (1 - centre) + q * centre) for p, q in zip(petal, (255, 176, 40)))
    a = body + (1 - body) * halo
    if a <= 0:
        return (0, 0, 0, 0)
    # Mix the white body with the dark green halo by their share of the alpha (straight alpha).
    shade = (24, 60, 32)
    w = body / a
    return (*(round(p * w + s * (1 - w)) for p, s in zip(rgb, shade)), round(a * 255))

def sheet(x, y):
    """Render test sheet, 2x2 cells of 64: a soft leaf at top left; the other cells are opaque
    magenta to their edges, so any bleed across the crop shows as a magenta line."""
    if x >= 64 or y >= 64:
        return (255, 0, 255, 255)
    def leaf(px, py):
        u, v = (px - 32) / 26, (py - 32) / 14
        ru, rv = (u + v * .55) / 1.1, (v - u * .35)  # tilted
        return ru * ru + rv * rv * 1.6 < 1
    a = coverage(leaf, x, y)
    vein = abs((y + .5 - 32) - (x + .5 - 32) * .3) < 1.2 and abs(x - 32) < 20
    colour = (40, 120, 52) if vein else (96, 196, 80)
    return (*colour, round(a * 255)) if a > 0 else (0, 0, 0, 0)

def bake_font(folder):
    """Bakes FONT at each of FONT_SIZES into fredoka-<size>.bmp, with fredoka.font, the metrics:
    `bake <pixels> <sheet> <ascent> <descent>` then one `glyph <code> <x> <y> <w> <h> <left> <top>
    <advance>` per printable ASCII character; left/top place the padded box from the pen on the
    baseline (top is negative above it), all in that bake's pixels."""
    from PIL import Image, ImageDraw, ImageFont
    lines = [f'# {FONT.name} weight {FONT_WEIGHT}, baked by scripts/generate-assets.py; licence OFL.txt']
    for pixels in FONT_SIZES:
        font = ImageFont.truetype(str(FONT), pixels)
        font.set_variation_by_axes([FONT_WEIGHT, 100])
        ascent, descent = font.getmetrics()
        boxes = []
        for code in range(32, 127):
            ch = chr(code)
            left, top, right, bottom = font.getbbox(ch, anchor='ls')
            image = None
            if right > left and bottom > top:
                w, h = right - left + 2 * GLYPH_PAD, bottom - top + 2 * GLYPH_PAD
                image = Image.new('L', (w, h))
                ImageDraw.Draw(image).text((GLYPH_PAD - left, GLYPH_PAD - top), ch, fill=255, font=font, anchor='ls')
            boxes.append((code, image, left - GLYPH_PAD, top - GLYPH_PAD, font.getlength(ch)))
        # Shelf packing, tallest first, for a sheet a few rows tall.
        width = 256 if pixels <= 24 else 512 if pixels <= 54 else 1024
        placed, x, y, shelf = {}, 0, 0, 0
        for code, image, *_ in sorted(boxes, key=lambda b: (-(b[1].height if b[1] else 0), b[0])):
            if not image:
                continue
            if x + image.width > width:
                x, y, shelf = 0, y + shelf, 0
            placed[code] = (x, y)
            x, shelf = x + image.width, max(shelf, image.height)
        height = y + shelf
        canvas = Image.new('L', (width, height))
        for code, image, *_ in boxes:
            if image:
                canvas.paste(image, placed[code])
        name = f'fredoka-{pixels}.bmp'
        bmp8(folder / name, width, height, canvas.tobytes())
        lines.append(f'bake {pixels} {name} {ascent} {descent}')
        for code, image, left, top, advance in boxes:
            sx, sy = placed.get(code, (0, 0))
            w, h = (image.width, image.height) if image else (0, 0)
            lines.append(f'glyph {code} {sx} {sy} {w} {h} {left} {top} {advance:.2f}')
    (folder / 'fredoka.font').write_text('\n'.join(lines) + '\n', newline='\n')

def main():
    catalog = ROOT / 'platform/ios/Assets.xcassets'
    icon = catalog / 'AppIcon.appiconset'
    icon.mkdir(parents=True, exist_ok=True)
    png(icon / 'icon.png', 1024)
    (catalog / 'Contents.json').write_text(json.dumps({'info': {'author': 'xcode', 'version': 1}}, indent=2))
    (icon / 'Contents.json').write_text(json.dumps({'images': [{'filename': 'icon.png', 'idiom': 'universal', 'platform': 'ios', 'size': '1024x1024'}], 'info': {'author': 'xcode', 'version': 1}}, indent=2))
    ASSETS.mkdir(parents=True, exist_ok=True)
    pixels = bytearray()
    for y in reversed(range(16)):
        for x in range(16):
            rgb = (166, 255, 226) if (x-8)**2+(y-8)**2 <= 36 else (70, 235, 196)
            pixels.extend(reversed(rgb))
    header = b'BM' + struct.pack('<IHHI', 54+len(pixels), 0, 0, 54)
    dib = struct.pack('<IiiHHIIiiII', 40, 16, 16, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    (ASSETS / 'spark.bmp').write_bytes(header + dib + pixels)
    # The renderer's own test art (YY_TAPDEMO_SCENE=render-test), not game art.
    tests = ASSETS / 'render-test'
    tests.mkdir(exist_ok=True)
    bmp32(tests / 'cutout.bmp', 128, 128, flower)
    bmp32(tests / 'sheet.bmp', 128, 128, sheet)
    bake_font(ASSETS / 'fonts')

if __name__ == '__main__':
    main()

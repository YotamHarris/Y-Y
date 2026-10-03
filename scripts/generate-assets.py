"""Deterministic placeholder assets; no external graphics libraries required."""
from pathlib import Path
import json
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]

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

def main():
    catalog = ROOT / 'platform/ios/Assets.xcassets'
    icon = catalog / 'AppIcon.appiconset'
    icon.mkdir(parents=True, exist_ok=True)
    png(icon / 'icon.png', 1024)
    (catalog / 'Contents.json').write_text(json.dumps({'info': {'author': 'xcode', 'version': 1}}, indent=2))
    (icon / 'Contents.json').write_text(json.dumps({'images': [{'filename': 'icon.png', 'idiom': 'universal', 'platform': 'ios', 'size': '1024x1024'}], 'info': {'author': 'xcode', 'version': 1}}, indent=2))
    assets = ROOT / 'games/tapdemo/assets'
    assets.mkdir(parents=True, exist_ok=True)
    pixels = bytearray()
    for y in reversed(range(16)):
        for x in range(16):
            rgb = (166, 255, 226) if (x-8)**2+(y-8)**2 <= 36 else (70, 235, 196)
            pixels.extend(reversed(rgb))
    header = b'BM' + struct.pack('<IHHI', 54+len(pixels), 0, 0, 54)
    dib = struct.pack('<IiiHHIIiiII', 40, 16, 16, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    (assets / 'spark.bmp').write_bytes(header + dib + pixels)

if __name__ == '__main__':
    main()

import struct, zlib, os

def make_png(width, height, pixels):
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)
    rows = []
    for y in range(height):
        row = bytes(pixels[y*width*4:(y+1)*width*4])
        rows.append(b"\x00" + row)
    compressed = zlib.compress(b"".join(rows))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", compressed) +
            chunk(b"IEND", b""))

W = H = 64
px = [0] * (W * H * 4)

def sp(x, y, r, g, b, a=255):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 4
        px[i], px[i+1], px[i+2], px[i+3] = r, g, b, a

# Globe fill
for y in range(H):
    for x in range(W):
        cx, cy = x - 32, y - 32
        if cx*cx + cy*cy <= 29*29:
            sp(x, y, 26, 82, 118, 255)

# Grid lines
for y in range(H):
    for x in range(W):
        cx, cy = x-32, y-32
        if cx*cx + cy*cy <= 29*29:
            if abs(cy) < 1 or abs(cy - 14) < 1 or abs(cy + 14) < 1:
                sp(x, y, 52, 152, 219, 200)
            if abs(cx) < 1 or abs(cx - 14) < 1 or abs(cx + 14) < 1:
                sp(x, y, 52, 152, 219, 200)

# P letter (white)
for y in range(22, 42):
    sp(20, y, 255,255,255,255); sp(21, y, 255,255,255,255)
for x in range(20, 28):
    sp(x, 22, 255,255,255,255); sp(x, 23, 255,255,255,255)
    sp(x, 31, 255,255,255,255); sp(x, 32, 255,255,255,255)
for y in range(22, 33):
    sp(27, y, 255,255,255,255); sp(28, y, 255,255,255,255)

# G letter (white)
for y in range(22, 42):
    sp(34, y, 255,255,255,255); sp(35, y, 255,255,255,255)
for x in range(34, 43):
    sp(x, 22, 255,255,255,255); sp(x, 23, 255,255,255,255)
    sp(x, 40, 255,255,255,255); sp(x, 41, 255,255,255,255)
for y in range(31, 42):
    sp(42, y, 255,255,255,255); sp(43, y, 255,255,255,255)
for x in range(38, 44):
    sp(x, 31, 255,255,255,255); sp(x, 32, 255,255,255,255)

# Globe border
for y in range(H):
    for x in range(W):
        cx, cy = x-32, y-32
        d2 = cx*cx + cy*cy
        if 27*27 <= d2 <= 29*29:
            sp(x, y, 41, 128, 185, 255)

data = make_png(W, H, px)
os.makedirs("qgis_plugin", exist_ok=True)
with open("qgis_plugin/icon.png", "wb") as f:
    f.write(data)
print(f"icon.png written — {len(data)} bytes")

import json
import math
import io
from PIL import Image, ImageDraw, ImageFont

SRC = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_pilot.json"
OUT = r"D:\Projects\AssetPipeline-EngineAgnostic\docs\placed_pilot_topdown.png"

with io.open(SRC, encoding="utf-8-sig") as fh:
    data = json.load(fh)
items = data["items"]
n = len(items)

xs = [it["t"][0] for it in items]
ys = [it["t"][1] for it in items]
minx, maxx = min(xs), max(xs)
miny, maxy = min(ys), max(ys)
pad = 3.0
W = H = 980
scale_px = min((W - 90) / (maxx - minx + 2 * pad), (H - 130) / (maxy - miny + 2 * pad))

def px(x, y):
    return (45 + (x - (minx - pad)) * scale_px, H - 60 - (y - (miny - pad)) * scale_px)

img = Image.new("RGB", (W, H), (18, 20, 26))
d = ImageDraw.Draw(img)
font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
font_s = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 15)

# bbox
x0, y0 = px(minx, miny)
x1, y1 = px(maxx, maxy)
d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], outline=(90, 200, 255), width=1)

# grid
for gx in range(int(math.floor(minx - pad)), int(math.ceil(maxx + pad)) + 1, 5):
    a = px(gx, miny - pad)
    b = px(gx, maxy + pad)
    d.line([a, b], fill=(38, 42, 52), width=1)
for gy in range(int(math.floor(miny - pad)), int(math.ceil(maxy + pad)) + 1, 5):
    a = px(minx - pad, gy)
    b = px(maxx + pad, gy)
    d.line([a, b], fill=(38, 42, 52), width=1)

smin = min(it["s"][0] for it in items)
smax = max(it["s"][0] for it in items)
for it in items:
    x, y, z = it["t"]
    s = it["s"][0]
    yaw = math.radians(it["r"][2])
    half = 0.5 * s
    corners = []
    for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half)):
        rx = dx * math.cos(yaw) - dy * math.sin(yaw)
        ry = dx * math.sin(yaw) + dy * math.cos(yaw)
        corners.append(px(x + rx, y + ry))
    k = (s - smin) / (smax - smin + 1e-9)
    col = (int(120 + 110 * k), int(190 - 60 * k), int(120 + 60 * k))
    d.polygon(corners, fill=col, outline=(25, 28, 34))

d.text((45, 18), "EAP · placements 回灌：manifest 233 条 → UE ISM 233 实例", font=font, fill=(230, 235, 245))
d.text((45, H - 42), "世界坐标（m，Z 朝上，此图为 XY 俯视）；方块 = 1 m Cube × scale，按 yaw 旋转；"
                      "蓝框 = manifest items bbox", font=font_s, fill=(150, 160, 180))
d.text((45, 44), "关卡 /Game/EAP/PCG/PlacedPilot · Actor EAP_Placements · ISM · "
                 "逐条 transform 与 manifest 一致", font=font_s, fill=(150, 160, 180))
d.text((45, 66), "scale 范围 %.3f ~ %.3f；bbox min=(%.2f, %.2f) max=(%.2f, %.2f)"
       % (smin, smax, minx, miny, maxx, maxy), font=font_s, fill=(150, 160, 180))
img.save(OUT)
print("OK %s %d instances" % (OUT, n))

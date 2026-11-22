"""EAP · 落位俯视图（PNG）：从 place_manifest 读回的实例 transform 画 XY 平面分布。

    # 引擎 Cube 占位（阶段一前半程）
    python tools/draw_placements.py

    # 真实树资产（阶段一收尾）
    python tools/draw_placements.py --src docs/placed_tree.json --out docs/placed_tree_topdown.png \
        --marker circle --radius-m 2.9 --height-m 10.236 --kind "conifer 3370 面" \
        --level /Game/EAP/PCG/PlacedTrees

用 bundled python 跑（需要 Pillow）。
"""
import argparse
import io
import json
import math
import os

from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = r"C:\Windows\Fonts\msyh.ttc"


def load(path):
    with io.open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return data["items"], data


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(REPO, "docs", "placed_pilot.json"))
    ap.add_argument("--out", default=os.path.join(REPO, "docs", "placed_pilot_topdown.png"))
    ap.add_argument("--marker", choices=("square", "circle"), default="square")
    ap.add_argument("--radius-m", type=float, default=None, help="circle 标记的半径（米），按实例 scale 缩放")
    ap.add_argument("--height-m", type=float, default=None, help="资产高度（米），只用于图注")
    ap.add_argument("--kind", default="1 m Cube 占位", help="图注里写的资产类型")
    ap.add_argument("--level", default="/Game/EAP/PCG/PlacedPilot", help="图注里写的关卡")
    ap.add_argument("--note", default=None, help="额外图注（可选）")
    args = ap.parse_args(argv[1:])

    items, data = load(args.src)
    n = len(items)
    xs = [it["t"][0] for it in items]
    ys = [it["t"][1] for it in items]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = 3.0
    W = H = 980
    L, R, T, B = 45, 45, 118, 72
    scale_px = min((W - L - R) / (maxx - minx + 2 * pad), (H - T - B) / (maxy - miny + 2 * pad))

    def px(x, y):
        return (L + (x - (minx - pad)) * scale_px, H - B - (y - (miny - pad)) * scale_px)

    img = Image.new("RGB", (W, H), (18, 20, 26))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, 20)
    font_s = ImageFont.truetype(FONT, 15)

    x0, y0 = px(minx, miny)
    x1, y1 = px(maxx, maxy)
    d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], outline=(90, 200, 255), width=1)
    for gx in range(int(math.floor(minx - pad)), int(math.ceil(maxx + pad)) + 1, 5):
        d.line([px(gx, miny - pad), px(gx, maxy + pad)], fill=(38, 42, 52), width=1)
    for gy in range(int(math.floor(miny - pad)), int(math.ceil(maxy + pad)) + 1, 5):
        d.line([px(minx - pad, gy), px(maxx + pad, gy)], fill=(38, 42, 52), width=1)

    smin = min(it["s"][0] for it in items)
    smax = max(it["s"][0] for it in items)
    for it in items:
        x, y, z = it["t"]
        s = it["s"][0]
        k = (s - smin) / (smax - smin + 1e-9)
        if args.marker == "circle":
            # 树冠：深绿 → 浅绿，尺寸编码 scale
            col = (int(46 + 60 * k), int(104 + 84 * k), int(58 + 52 * k))
        else:
            # 方块：沿用蓝绿渐变
            col = (int(120 + 110 * k), int(190 - 60 * k), int(120 + 60 * k))
        if args.marker == "circle":
            r_m = (args.radius_m or 2.0) * s
            cx, cy = px(x, y)
            r_px = r_m * scale_px
            d.ellipse([cx - r_px, cy - r_px, cx + r_px, cy + r_px], fill=col, outline=(24, 34, 26))
        else:
            yaw = math.radians(it["r"][2])
            half = 0.5 * s
            corners = []
            for dx, dy in ((-half, -half), (half, -half), (half, half), (-half, half)):
                rx = dx * math.cos(yaw) - dy * math.sin(yaw)
                ry = dx * math.sin(yaw) + dy * math.cos(yaw)
                corners.append(px(x + rx, y + ry))
            d.polygon(corners, fill=col, outline=(25, 28, 34))

    marker_desc = ("圆 = 树冠投影（半径 %.2f m × scale）" % (args.radius_m or 2.0)
                   if args.marker == "circle" else "方块 = 1 m Cube × scale")
    d.text((L, 18), "EAP · placements 回灌：%s → UE ISM %d 实例" % (args.kind, n), font=font, fill=(230, 235, 245))
    d.text((L, 44), "关卡 %s · Actor EAP_Placements · ISM · 逐条 transform 与 manifest 一致" % args.level,
           font=font_s, fill=(150, 160, 180))
    d.text((L, 66), "scale 范围 %.3f ~ %.3f；bbox min=(%.2f, %.2f) max=(%.2f, %.2f)%s"
           % (smin, smax, minx, miny, maxx, maxy,
              ("；资产高 %.3f m" % args.height_m) if args.height_m else ""),
           font=font_s, fill=(150, 160, 180))
    if args.note:
        d.text((L, 88), args.note, font=font_s, fill=(120, 200, 140))
    foot = "世界坐标（m，Z 朝上，XY 俯视）；%s；蓝框 = manifest items bbox" % marker_desc
    d.text((L, H - 42), foot, font=font_s, fill=(150, 160, 180))
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    img.save(args.out)
    print("OK %s %d instances" % (args.out, n))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))

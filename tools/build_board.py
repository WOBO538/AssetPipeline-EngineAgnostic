"""把 board/status.json 注入 board/board.html.tpl，生成桌面单页看板。

为什么要注入：浏览器在 file:// 下禁止 fetch 同目录的 JSON（CORS），
所以数据在构建时直接写进 HTML，桌面只留一个文件，双击即开、离线可用。

用法：
    python tools/build_board.py                  # 输出到桌面
    python tools/build_board.py --out D:\\x.html  # 指定路径
"""
import argparse
import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "board", "board.html.tpl")
STATUS = os.path.join(ROOT, "board", "status.json")


def desktop_candidates():
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Desktop"),
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "OneDrive", "桌面"),
        os.path.join(home, "桌面"),
    ]


def resolve_desktop():
    for path in desktop_candidates():
        if os.path.isdir(path):
            return path
    raise SystemExit("找不到桌面目录，请用 --out 指定输出路径")


def build(out_path=None):
    with open(STATUS, encoding="utf-8-sig") as fh:
        status = json.load(fh)
    with open(TPL, encoding="utf-8") as fh:
        tpl = fh.read()

    data_json = json.dumps(status, ensure_ascii=False, separators=(",", ":"))
    # 防止 JSON 里出现 </script> 提前闭合
    data_json = data_json.replace("</", "<\\/")
    out = (tpl
           .replace("__STATUS_JSON__", data_json)
           .replace("__UPDATED__", html.escape(status.get("updatedAt", "")))
           .replace("__TODAY__", html.escape(status.get("today", "")))
           .replace("__HEADLINE__", html.escape(status.get("headline", ""))))

    if not out_path:
        out_path = os.path.join(resolve_desktop(), "EAP进度看板.html")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    path = build(args.out)
    print("OK " + path)
    print("size=%d bytes" % os.path.getsize(path))
    sys.exit(0)

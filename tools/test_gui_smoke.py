"""GUI 阶段一冒烟测试（离屏跑，不用人看也能留证据）

断言三件事：
1. 主窗口能创建、三栏控件齐全、窗口尺寸正确
2. 防呆：未选文件 → Toast 文案含"先拖入"；有文件无提示词 → 含"提示词不能为空"
3. 都满足时能走到"已提交"（阶段一无后端，只写日志）

产物：docs/gui_phase1.png（离屏截图，作为验收证据）
用法：python tools/test_gui_smoke.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.app import create_window  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    app = QApplication(sys.argv)
    win = create_window()
    win.resize(1180, 720)
    win.show()
    app.processEvents()

    # 1. 结构
    check("窗口尺寸=1180x720", (win.width(), win.height()) == (1180, 720), str((win.width(), win.height())))
    check("三个滑块齐全", sorted(win.sliders) == ["cel", "fresnel", "outline"], str(sorted(win.sliders)))
    check("预览区存在", win.preview is not None and win.preview.objectName() == "Preview")
    check("日志区只读", win.log_view.isReadOnly())
    check("进度条 0-100", (win.progress.minimum(), win.progress.maximum()) == (0, 100))

    # 2. 防呆：未选文件
    win.on_start()
    app.processEvents()
    check("防呆1 未选文件被拦", win.toast.isVisible() and "先拖入" in win.toast.text(), win.toast.text())
    check("防呆1 状态未变", win.status.text() == "空闲", win.status.text())

    # 防呆：有文件、无提示词
    win.toast.hide()
    win.on_file_chosen(os.path.join(ROOT, "schemas", "manifest.example.json"))
    win.on_start()
    app.processEvents()
    check("防呆2 提示词为空被拦", win.toast.isVisible() and "提示词" in win.toast.text(), win.toast.text())

    # 3. 正常路径
    win.toast.hide()
    win.prompt.setText("wooden chest, iron banding, anime cel shading")
    win.on_start()
    app.processEvents()
    check("正常路径已提交", win.status.text() == "已提交", win.status.text())
    log_text = win.log_view.toPlainText()
    check("日志含提交记录", "提交生成" in log_text)
    check("日志含参数", "阈值=55" in log_text and "描边=2" in log_text and "高光=40" in log_text)

    # 截图（证据）
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    shot = os.path.join(ROOT, "docs", "gui_phase1.png")
    win.grab().save(shot, "PNG")
    check("截图已保存", os.path.isfile(shot) and os.path.getsize(shot) > 8000,
          "%s %d bytes（离屏无字体，尺寸偏小属预期；带字体截图见 gui_phase1_win.png）"
          % (shot, os.path.getsize(shot) if os.path.isfile(shot) else 0))

    print("\n%s (%d fails)" % ("ALL PASS" if not FAILS else "FAILED", len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())

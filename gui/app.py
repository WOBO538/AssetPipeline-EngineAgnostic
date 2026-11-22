"""EAP GUI · 主窗口（阶段一：UI 框架 + 防呆，不接后端）

布局：左（输入/拖拽）｜中（预览 + 进度 + 提示词/生成）｜右（参数滑块 + 日志）
阶段一只负责：能启动、能截图、三种错误输入都被拦住；生成行为留到阶段二。
"""
import os
import sys
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar,
    QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gui.widgets import DropZone, Toast  # noqa: E402


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EAP · 二次元资产管线调试面板")
        self.resize(1180, 720)
        self.current_file = ""
        self.toast = Toast(self)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)
        outer.addWidget(self._build_left())
        outer.addWidget(self._build_center(), 1)
        outer.addWidget(self._build_right())

        self.log("面板就绪。阶段一：UI 与防呆已接通，生成后端未接入。")

    # ---------- 三栏 ----------
    def _panel(self, title: str, width: int = 0) -> tuple[QWidget, QVBoxLayout]:
        panel = QWidget()
        panel.setObjectName("Panel")
        if width:
            panel.setFixedWidth(width)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        lay.addWidget(label)
        return panel, lay

    def _build_left(self) -> QWidget:
        panel, lay = self._panel("输入与资产", 300)
        self.drop = DropZone()
        self.drop.fileDropped.connect(self.on_file_chosen)
        self.drop.rejected.connect(lambda msg: self.toast.show_message(msg))
        lay.addWidget(self.drop)

        self.pick_btn = QPushButton("选择文件…")
        self.pick_btn.clicked.connect(self.on_pick_file)
        self.demo_btn = QPushButton("载入示例 manifest")
        self.demo_btn.clicked.connect(self.on_pick_manifest)
        lay.addWidget(self.pick_btn)
        lay.addWidget(self.demo_btn)

        self.file_label = QLabel("未选择文件")
        self.file_label.setObjectName("Status")
        self.file_label.setWordWrap(True)
        lay.addWidget(self.file_label)
        lay.addStretch(1)
        return panel

    def _build_center(self) -> QWidget:
        panel, lay = self._panel("预览", 0)
        self.preview = QLabel("暂无预览\n（阶段二接入 ComfyUI 后回填）")
        self.preview.setObjectName("Preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(360)
        lay.addWidget(self.preview, 1)

        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        row.addWidget(self.progress, 1)
        self.status = QLabel("空闲")
        self.status.setObjectName("Status")
        row.addWidget(self.status)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("提示词：例如 wooden chest, iron banding, anime cel shading")
        row2.addWidget(self.prompt, 1)
        self.start_btn = QPushButton("开始生成")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.on_start)
        row2.addWidget(self.start_btn)
        lay.addLayout(row2)
        return panel

    def _build_right(self) -> QWidget:
        panel, lay = self._panel("参数与日志", 320)
        self.sliders = {}
        for key, title, lo, hi, default, suffix in (
            ("cel", "卡渲阴影阈值 Cel Threshold", 0, 100, 55, "%"),
            ("outline", "描边粗细 Outline Width", 0, 10, 2, "px"),
            ("fresnel", "高光强度 Fresnel", 0, 100, 40, "%"),
        ):
            row = QHBoxLayout()
            name = QLabel(title)
            name.setObjectName("Status")
            row.addWidget(name, 1)
            value = QLabel(f"{default}{suffix}")
            value.setObjectName("ValueTag")
            row.addWidget(value)
            lay.addLayout(row)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(default)
            slider.valueChanged.connect(
                lambda v, l=value, s=suffix: l.setText(f"{v}{s}")
            )
            self.sliders[key] = slider
            lay.addWidget(slider)

        self.log_view = QTextEdit()
        self.log_view.setObjectName("Log")
        self.log_view.setReadOnly(True)
        lay.addWidget(self.log_view, 1)
        return panel

    # ---------- 行为 ----------
    def log(self, text: str):
        self.log_view.append(f"[{datetime.now():%H:%M:%S}] {text}")

    def on_pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择白模或参考图", "",
            "模型/图片 (*.obj *.fbx *.glb *.png *.jpg *.jpeg *.webp)")
        if path:
            self.on_file_chosen(path)

    def on_pick_manifest(self):
        default = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "schemas", "manifest.example.json")
        path, _ = QFileDialog.getOpenFileName(self, "选择 manifest.json", default, "manifest (*.json)")
        if path:
            self.on_file_chosen(path)

    def on_file_chosen(self, path: str):
        self.current_file = path
        name = os.path.basename(path)
        self.file_label.setText(name)
        self.log(f"载入输入：{name}")
        self.drop.setText(f"已载入\n{name}")

    def on_start(self):
        """防呆三连：没选文件 / 没写提示词 / 都满足才放行。"""
        if not self.current_file:
            self.toast.show_message("先拖入或选择一个文件（.obj/.fbx/.glb/图片）")
            self.log("拦截：未选择输入文件")
            return
        if not self.prompt.text().strip():
            self.toast.show_message("提示词不能为空")
            self.log("拦截：提示词为空")
            return
        self.status.setText("已提交")
        self.log(f"提交生成（阶段一未接后端）：{os.path.basename(self.current_file)}，"
                 f"阈值={self.sliders['cel'].value()} 描边={self.sliders['outline'].value()} "
                 f"高光={self.sliders['fresnel'].value()}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast.show_message(self.toast.text())


def create_window() -> MainWindow:
    win = MainWindow()
    qss = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.qss")
    if os.path.isfile(qss):
        with open(qss, encoding="utf-8") as fh:
            win.setStyleSheet(fh.read())
    return win

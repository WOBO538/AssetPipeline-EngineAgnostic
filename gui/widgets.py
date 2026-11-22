"""EAP GUI · 通用控件：拖拽区、Toast 提示。"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QWidget

ALLOWED_SUFFIX = (".obj", ".fbx", ".glb", ".png", ".jpg", ".jpeg", ".webp")


class DropZone(QLabel):
    """拖入白模/参考图。接受后抛 fileDropped，非法格式抛 rejected。"""

    fileDropped = Signal(str)
    rejected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.setText("拖入 .obj / .fbx / .glb\n或参考图（png/jpg）")
        self.setMinimumHeight(140)

    def _first_path(self, event) -> str:
        urls = event.mimeData().urls()
        return urls[0].toLocalFile() if urls else ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            self.rejected.emit("拖入的内容不是文件")

    def dragLeaveEvent(self, event):
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        path = self._first_path(event)
        if not path:
            self.rejected.emit("拖入的内容没有本地路径")
            return
        if not path.lower().endswith(ALLOWED_SUFFIX):
            self.rejected.emit("不支持的格式：仅接受 obj/fbx/glb/图片")
            return
        self.fileDropped.emit(path)


class Toast(QLabel):
    """顶部居中的红色提示，2.2 秒自动消失（防呆反馈用它，不用 modal）。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = 2200):
        self.setText(text)
        self.adjustSize()
        p = self.parentWidget()
        x = max(0, (p.width() - self.width()) // 2)
        self.move(x, 18)
        self.show()
        self.raise_()
        self._timer.start(ms)

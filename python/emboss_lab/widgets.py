from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ._native import STRIP_COUNT, WORKER_COUNTS


class Preview(QWidget):
    def __init__(self, message: str):
        super().__init__()
        self.image = QImage()
        self.setMinimumHeight(112)
        self.message = QLabel(message)
        self.message.setObjectName("placeholder")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon = QWidget()
        self.icon.setFixedSize(32, 24)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(10)
        layout.addStretch()
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.message, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()

    def set_image(self, image: QImage | None):
        self.image = QImage() if image is None else image
        self.message.setVisible(self.image.isNull())
        self.icon.setVisible(self.image.isNull())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#F0EEEA"))
        painter.setPen(QColor("#D9D8D2"))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if not self.image.isNull():
            inside = self.rect().adjusted(1, 1, -1, -1)
            size = self.image.size().scaled(inside.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(0, 0, max(size.width(), 1), max(size.height(), 1))
            target.moveCenter(QRectF(inside).center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.image)
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#858F8A"), 1.4))
        x, y = self.icon.x(), self.icon.y()
        painter.drawRoundedRect(QRectF(x, y, 32, 24), 4, 4)
        painter.drawEllipse(QRectF(x + 20, y + 5, 4, 4))
        painter.drawLine(QRectF(x + 4, y + 10, 7, 8).bottomLeft(),
                         QRectF(x + 4, y + 10, 7, 8).topRight())
        painter.drawLine(QRectF(x + 11, y + 10, 8, 8).topLeft(),
                         QRectF(x + 11, y + 10, 8, 8).bottomRight())


class StripMeter(QWidget):
    colors = {
        "off": "#E7E5E0",
        "waiting": "#D6D8D3",
        "working": "#537565",
        "done": "#87A293",
        "stopped": "#B6A38B",
    }

    def __init__(self):
        super().__init__()
        self.strips = []
        self.setMinimumSize(QSize(84, 24))
        self.setAccessibleName("Карта фрагментов")

    def set_strips(self, strips: list[str]):
        if strips != self.strips:
            self.strips = strips
            counts = [strips.count(state) for state in ("done", "working", "waiting")]
            self.setToolTip(f"Готово {counts[0]} · в работе {counts[1]} · ожидают {counts[2]}")
            self.update()

    def paintEvent(self, event):
        states = self.strips or ["off"] * STRIP_COUNT
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        step = self.width() / len(states)
        width = min(13, 0.74 * step)
        for i, state in enumerate(states):
            height = 22 if state == "working" else 18
            painter.setBrush(QColor(self.colors[state]))
            painter.drawRoundedRect(
                QRectF((i + 0.5) * step - width / 2, (self.height() - height) / 2, width, height),
                1.5, 1.5,
            )


class ThreadIndicators(QWidget):
    states = {
        "off": ("не запущен", "#E7E5E0"),
        "waiting": ("ожидает", "#D6D8D3"),
        "working": ("работает", "#537565"),
        "done": ("завершил работу", "#87A293"),
        "stopped": ("остановлен", "#B6A38B"),
    }

    def __init__(self):
        super().__init__()
        self.items = []
        self.previous = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for index in range(max(WORKER_COUNTS)):
            chip = QWidget()
            chip.setFixedWidth(21)
            column = QVBoxLayout(chip)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            label = QLabel(f"T{index + 1}")
            label.setObjectName("threadId")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar = QFrame()
            bar.setFixedHeight(5)
            column.addWidget(label)
            column.addWidget(bar)
            row.addWidget(chip)
            self.items.append((chip, bar))
        self.set_states([])

    def set_states(self, states: list[str]):
        if states == self.previous:
            return
        self.previous = states
        descriptions = []
        for index, (chip, bar) in enumerate(self.items):
            state = states[index] if index < len(states) else "off"
            description, color = self.states[state]
            text = f"T{index + 1} — {description}"
            descriptions.append(text)
            chip.setToolTip(text)
            chip.setAccessibleName(text)
            bar.setStyleSheet(f"background: {color}; border-radius: 2px;")
        self.setAccessibleName(", ".join(descriptions))

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QImage

from ._native import Processor
from .image_io import FileCancellation, RgbImage, load_bmp

PREVIEW_LIMIT = 2400

# Фоновая задача на основе QThread, сначала окном передаётся задаче функция чтения
class BackgroundJob(QThread):
    def __init__(self, kind: str, operation: Callable, parent):
        super().__init__(parent)
        self.kind = kind
        self.operation = operation
        self.value = None
        self.error = None  # Окно читает value и error после посыла Qt сигнала finished.

    def run(self):
        try:
            self.value = self.operation()
        except Exception as error:
            self.error = error


def make_preview(image: RgbImage) -> QImage:
    preview = QImage(
        image.pixels, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888
    )  # Шаг строки задан явно, поскольку RGB-буфер плотный.
    # Масштабируем только предпросмотр. Полный RGB нужен для обработки и записи.
    longest = max(image.width, image.height)
    if longest > PREVIEW_LIMIT:
        preview = preview.scaled(
            max(1, image.width * PREVIEW_LIMIT // longest),
            max(1, image.height * PREVIEW_LIMIT // longest),
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
    else:
        preview = preview.copy()  # Копия владеет пикселями независимо от исходного RGB.
    if preview.isNull():
        raise MemoryError("Не хватает памяти для предпросмотра")
    return preview


def read_with_preview(path: Path, cancellation: FileCancellation) -> tuple[RgbImage, QImage]:
    cancellation.check()
    image = load_bmp(path)
    cancellation.check()  # Учитываем запрос, поступивший во время декодирования.
    return image, make_preview(image)


def process_with_preview(
    image: RgbImage, processor: Processor, workers: int # Окно создаёт объект Processor, который управляет одним запуском расчёта
) -> tuple[RgbImage, QImage]:
    pixels = processor.process(image.pixels, image.width, image.height, workers)
    result = RgbImage(image.width, image.height, pixels)
    return result, make_preview(result)

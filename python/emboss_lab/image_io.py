import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import BinaryIO

from PIL import Image

from ._native import MAX_DIMENSION, MAX_PIXELS, ProcessingCancelled


class BmpError(Exception):
    pass


@dataclass(frozen=True)
class RgbImage:
    width: int
    height: int
    pixels: bytes


class FileCancellation:
    def __init__(self):
        self._lock = Lock()
        self._requested = False
        self._publishing = False

    def cancel(self) -> bool:
        with self._lock:
            if self._publishing:
                return False
            self._requested = True
            return True

    def check(self):
        with self._lock:
            if self._requested:
                raise ProcessingCancelled("Файловая операция отменена")

    def begin_publish(self):
        # Отмена и решение о публикации взаимоисключающие. Диск под lock не вызывается.
        with self._lock:
            if self._requested:
                raise ProcessingCancelled("Сохранение отменено")
            self._publishing = True


def _bmp_dimensions(stream: BinaryIO) -> tuple[int, int]:
    header = stream.read(54)
    if len(header) < 18 or header[:2] != b"BM":
        raise BmpError("Нужен BMP в формате RGB24")

    declared_size, _, _, pixel_offset, dib_size = struct.unpack_from("<IHHII", header, 2)
    if dib_size == 12 and len(header) >= 26:
        width, height, planes, bits = struct.unpack_from("<HHHH", header, 18)
        compression = 0
    elif dib_size in (40, 52, 56, 64, 108, 124) and len(header) >= 54:
        width, height, planes, bits, compression = struct.unpack_from("<iiHHI", header, 18)
        height = abs(height)
    else:
        raise BmpError("Не удалось прочитать заголовок BMP")

    if planes != 1 or bits != 24 or compression != 0:
        raise BmpError("Нужен несжатый BMP в формате RGB24")
    if width <= 0 or height <= 0:
        raise BmpError("В BMP указаны неверные размеры")
    if max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise BmpError("Изображение слишком большое. Лимит: 24 млн пикселей, до 16 384 на сторону")

    row_size = (width * 3 + 3) // 4 * 4  # Строки BMP дополнены до кратности четырём.
    required_size = pixel_offset + row_size * height
    actual_size = os.fstat(stream.fileno()).st_size
    if pixel_offset < 14 + dib_size or required_size > actual_size:
        raise BmpError("BMP повреждён: отсутствует часть данных")
    if declared_size and not required_size <= declared_size <= actual_size:
        raise BmpError("BMP повреждён: неверная длина файла")
    return width, height


def load_bmp(path: Path) -> RgbImage:
    try:
        with path.open("rb") as stream:
            size = _bmp_dimensions(stream)
            stream.seek(0)
            with Image.open(stream, formats=("BMP",)) as bitmap:
                if bitmap.mode != "RGB" or bitmap.size != size:
                    raise BmpError("Данные BMP не соответствуют заголовку RGB24")
                return RgbImage(*size, bitmap.tobytes()) # Получаем байты цветов
    except (OSError, ValueError, SyntaxError) as error:
        raise BmpError("Не удалось прочитать изображение") from error


def save_bmp(path: Path, image: RgbImage, cancellation: FileCancellation | None = None):
    if cancellation is None:
        cancellation = FileCancellation()
    cancellation.check()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".emboss-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            with Image.frombytes("RGB", (image.width, image.height), image.pixels) as bitmap:
                bitmap.save(stream, format="BMP")
        cancellation.begin_publish()
        os.replace(temporary, path)
    except (OSError, ValueError) as error:
        raise BmpError("Не удалось сохранить BMP") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

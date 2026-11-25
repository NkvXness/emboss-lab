import struct

import pytest
from PIL import Image

from emboss_lab._native import ProcessingCancelled
from emboss_lab.image_io import BmpError, FileCancellation, RgbImage, load_bmp, save_bmp

PIXELS = bytes(range(30))
IMAGE = RgbImage(5, 2, PIXELS)


def test_rgb24_round_trip_unicode_path_and_padding(tmp_path):
    path = tmp_path / "Исходное изображение.bmp"
    save_bmp(path, IMAGE)
    header = path.read_bytes()[:54]
    assert header.startswith(b"BM")
    assert struct.unpack_from("<HI", header, 28) == (24, 0)
    assert load_bmp(path) == IMAGE


def test_top_down_rgb24(tmp_path):
    path = tmp_path / "сверху вниз.bmp"
    save_bmp(path, IMAGE)
    data = bytearray(path.read_bytes())
    offset = struct.unpack_from("<I", data, 10)[0]
    struct.pack_into("<i", data, 22, -2)
    data[offset:] = data[offset + 16:] + data[offset:offset + 16]
    path.write_bytes(data)
    assert load_bmp(path) == IMAGE


def test_non_rgb24_and_renamed_file(tmp_path):
    path = tmp_path / "unsupported.bmp"
    Image.new("RGBA", (5, 2)).save(path)
    with pytest.raises(BmpError, match="RGB24"):
        load_bmp(path)
    Image.new("RGB", (5, 2)).save(path, format="PNG")
    with pytest.raises(BmpError, match="RGB24"):
        load_bmp(path)


@pytest.mark.parametrize("field,value,message", [
    (18, 17000, "слишком большое"),
    (22, 0, "неверные размеры"),
    (10, 2, "повреждён"),
    (30, 1, "несжатый"),
])
def test_invalid_bmp_header(tmp_path, field, value, message):
    path = tmp_path / "wrong.bmp"
    save_bmp(path, IMAGE)
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, field, value)
    path.write_bytes(data)
    with pytest.raises(BmpError, match=message):
        load_bmp(path)


def test_truncated_bmp(tmp_path):
    path = tmp_path / "short.bmp"
    save_bmp(path, IMAGE)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(BmpError, match="повреждён"):
        load_bmp(path)


def test_failed_replace_preserves_target(tmp_path, monkeypatch):
    path = tmp_path / "existing.bmp"
    path.write_bytes(b"previous content")

    def fail_replace(_source, _destination):
        raise PermissionError("Destination is locked")

    monkeypatch.setattr("emboss_lab.image_io.os.replace", fail_replace)
    with pytest.raises(BmpError, match="Не удалось сохранить BMP"):
        save_bmp(path, IMAGE)
    assert path.read_bytes() == b"previous content"
    assert list(tmp_path.iterdir()) == [path]


def test_cancel_after_encoding_before_publication(tmp_path, monkeypatch):
    path = tmp_path / "existing.bmp"
    path.write_bytes(b"previous content")
    token = FileCancellation()
    real_save = Image.Image.save

    def save_then_cancel(image, stream, **kwargs):
        real_save(image, stream, **kwargs)
        assert token.cancel()

    monkeypatch.setattr(Image.Image, "save", save_then_cancel)
    with pytest.raises(ProcessingCancelled):
        save_bmp(path, IMAGE, token)
    assert path.read_bytes() == b"previous content"
    assert list(tmp_path.iterdir()) == [path]

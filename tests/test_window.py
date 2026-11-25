from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from emboss_lab._native import WORKER_COUNTS
from emboss_lab.image_io import RgbImage, load_bmp, save_bmp
from emboss_lab.window import MainWindow


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    stylesheet = Path(__file__).parents[1] / "python" / "emboss_lab" / "theme.qss"
    instance.setStyleSheet(stylesheet.read_text(encoding="utf-8"))
    yield instance


def wait_for(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def click(button):
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


def test_open_process_save_and_measure(app, tmp_path, monkeypatch):
    source = tmp_path / "sample.bmp"
    output = tmp_path / "emboss.bmp"
    image = RgbImage(61, 43, bytes([8, 24, 52]) * 61 * 43)
    save_bmp(source, image)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(source), "BMP (*.bmp)"))
    monkeypatch.setattr(QFileDialog, "exec", lambda _: True)
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda _: [str(output)])

    window = MainWindow()
    window.show()
    try:
        assert not window.primary.isEnabled()
        click(window.open_button)
        wait_for(lambda: window.job is None)
        assert window.source == image
        assert not window.original_preview.image.isNull()
        click(window.primary)
        wait_for(lambda: window.job is None)
        assert window.percent_label.text() == "100%"
        assert window.primary.text() == "Сохранить BMP"
        assert not window.result_preview.image.isNull()
        click(window.journal_button)
        assert window.journal.isVisible()
        assert "Взял полосу" in window.log_text.toPlainText()
        click(window.journal_button)
        click(window.primary)
        wait_for(lambda: window.job is None)
        assert load_bmp(output) == window.result == RgbImage(61, 43, bytes([128]) * 61 * 43 * 3)
        click(window.tabs.button(1))
        click(window.bench_start)
        wait_for(lambda: window.job is None)
        assert list(window.measurements) == list(WORKER_COUNTS)
        assert all(len(row.samples_ms) == 5 for row in window.measurements.values())
        assert window.bench_table.item(3, 3).text() == "Завершено"
    finally:
        window.close()
        wait_for(lambda: not window.isVisible())


def test_ui_responsive_during_io_and_close_after_cancel(app, tmp_path, monkeypatch):
    import emboss_lab.window as window_module

    reading, release_read = Event(), Event()
    writing, release_write = Event(), Event()
    ticks = []
    real_read = window_module.read_with_preview
    path = tmp_path / "sample.bmp"
    save_bmp(path, RgbImage(3, 4, bytes(36)))

    def paused_read(path, cancellation):
        reading.set()
        if not release_read.wait(5):
            raise RuntimeError("Истёк срок ожидания в тесте")
        return real_read(path, cancellation)

    monkeypatch.setattr(window_module, "read_with_preview", paused_read)
    window = MainWindow()
    window.show()
    ticker = QTimer()
    ticker.timeout.connect(lambda: ticks.append(True))
    ticker.start(5)
    try:
        window.load_path(path)
        wait_for(lambda: reading.is_set() and len(ticks) >= 3)
        assert window.job.kind == "read"
        assert not window.open_button.isEnabled()
        click(window.cancel_button)
        assert window.cancelling
        release_read.set()
        wait_for(lambda: window.job is None)
        assert window.source is None and window.status.text() == "Чтение отменено"

        window.load_path(path)
        wait_for(lambda: window.job is None)
        click(window.primary)
        wait_for(lambda: window.job is None and window.result is not None)

        destination = tmp_path / "existing.bmp"
        destination.write_bytes(b"previous content")

        def paused_save(path, image, cancellation):
            writing.set()
            if not release_write.wait(5):
                raise RuntimeError("Истёк срок ожидания в тесте")
            save_bmp(path, image, cancellation)

        monkeypatch.setattr(window_module, "save_bmp", paused_save)
        ticks.clear()
        window.save_path(destination)
        wait_for(lambda: writing.is_set() and len(ticks) >= 3)
        click(window.cancel_button)
        release_write.set()
        wait_for(lambda: window.job is None)
        assert destination.read_bytes() == b"previous content"
        assert window.result is not None and window.status.text() == "Сохранение отменено"

        click(window.repeat_button)
        assert window.job.kind == "process"
        window.close()
        wait_for(lambda: not window.isVisible())
        assert window.job is None and window.result is None
    finally:
        release_read.set()
        release_write.set()
        ticker.stop()
        window.close()
        wait_for(lambda: not window.isVisible())

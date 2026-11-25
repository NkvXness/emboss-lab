from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ._native import KERNEL, STRIP_COUNT, WORKER_COUNTS, ProcessingCancelled, Processor
from .benchmarks import REPETITIONS, Benchmark, Measurement
from .image_io import FileCancellation, save_bmp
from .widgets import Preview, StripMeter, ThreadIndicators
from .workers import BackgroundJob, process_with_preview, read_with_preview

FRAME_MARGIN = 24
POLL_MS = 100
JOB_TITLES = {
    "read": "Чтение BMP…", "process": "Обрабатывается…",
    "save": "Сохранение…", "benchmark": "Идут замеры…",
}


def label(text: str, role: str) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(role)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text: str, role: str = "") -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(role)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def row_for(frame: QWidget, margins=(FRAME_MARGIN, 0, FRAME_MARGIN, 0), spacing=16):
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


class MainWindow(QMainWindow):
    benchmark_update = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("EMBOSS LAB")
        available = QGuiApplication.primaryScreen().availableGeometry()
        width = max(850, available.width() - 20)
        height = max(480, available.height() - 42)
        self.setMinimumSize(min(1080, width), min(720, height))
        self.resize(min(1440, width), min(900, height))
        self.source = None
        self.source_path = None
        self.result = None
        self.job = None
        self.cancel_job = None
        self.processor = None
        self.cancelling = False
        self.closing = False
        self.last_phase = ""
        self.measurements = {}

        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.build_header())
        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_workspace())
        bench_scroll = QScrollArea()
        bench_scroll.setFrameShape(QFrame.Shape.NoFrame)
        bench_scroll.setWidgetResizable(True)
        bench_scroll.setWidget(self.build_benchmarks())
        self.pages.addWidget(bench_scroll)
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.build_status())
        self.build_journal(shell)
        self.bind_shortcuts()

        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self.poll_pool)
        self.benchmark_update.connect(self.update_benchmark)
        self.reset_progress()
        self.refresh_controls()
        self.open_button.setFocus()

    def build_header(self):
        frame = QFrame()
        frame.setObjectName("header")
        frame.setFixedHeight(80)
        row = row_for(frame, spacing=14)
        brand = label("EMBOSS\nLAB", "brand")
        brand.setContentsMargins(0, 0, 10, 0)
        row.addWidget(brand)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedSize(1, 36)
        row.addWidget(divider)
        self.tabs = QButtonGroup(self)
        for index, title in enumerate(("Рабочая область", "Замеры")):
            tab = button(title, "tab")
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            self.tabs.addButton(tab, index)
            row.addWidget(tab)
        self.tabs.idClicked.connect(self.change_page)
        row.addStretch()
        self.open_button = button("Открыть BMP")
        self.open_button.clicked.connect(self.open_bmp)
        row.addWidget(self.open_button)
        return frame

    def build_workspace(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.build_toolbar())

        self.gallery = QWidget()
        self.gallery_row = row = row_for(self.gallery, (FRAME_MARGIN, 18, FRAME_MARGIN, 18), spacing=24)
        self.original_preview = Preview("Откройте BMP, чтобы увидеть\nисходное изображение")
        self.result_preview = Preview("Результат появится\nпосле обработки")
        self.file_label = label("", "secondary")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.file_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ready_label = label("", "readyMark")
        self.ready_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.build_panel("01 / ИСХОДНОЕ", self.file_label, self.original_preview), 1)
        row.addWidget(self.build_panel("02 / РЕЗУЛЬТАТ", self.ready_label, self.result_preview), 1)
        layout.addWidget(self.gallery, 1)
        layout.addWidget(self.build_progress())
        return page

    def build_toolbar(self):
        frame = QFrame()
        frame.setObjectName("toolbar")
        frame.setFixedHeight(88)
        self.toolbar_row = row = row_for(frame)
        heading = QWidget()
        column = QVBoxLayout(heading)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(label("Тиснение", "title"))
        meta = QWidget()
        details = row_for(meta, (0, 0, 0, 0), 4)
        details.addWidget(label("Вариант 11 · ядро 3×3", "secondary"))
        method = button("ⓘ", "iconButton")
        method.setFixedSize(32, 32)
        method.setAccessibleName("О методе")
        method.setToolTip("О методе (F1)")
        method.clicked.connect(self.show_method)
        details.addWidget(method)
        details.addStretch()
        column.addWidget(meta)
        row.addWidget(heading, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch()

        row.addWidget(label("Число потоков", "secondary"))
        self.thread_group = QButtonGroup(self)
        self.segments = QFrame()
        self.segments.setObjectName("segmented")
        self.segments.setFixedHeight(42)
        threads = row_for(self.segments, (3, 3, 3, 3), 2)
        for count in WORKER_COUNTS:
            segment = button(str(count), "segment")
            segment.setCheckable(True)
            segment.setChecked(count == 4)
            segment.setAccessibleName(f"Количество потоков — {count}")
            self.thread_group.addButton(segment, count)
            threads.addWidget(segment)
        row.addWidget(self.segments)
        row.addStretch()
        self.repeat_button = button("Обработать снова", "textAction")
        self.repeat_button.clicked.connect(self.start_processing)
        row.addWidget(self.repeat_button)
        self.primary = button("Начать обработку", "primary")
        self.primary.setFixedWidth(224)
        self.primary.clicked.connect(self.primary_clicked)
        row.addWidget(self.primary)
        return frame

    def build_panel(self, title: str, detail: QLabel, preview: Preview):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QWidget()
        header.setFixedHeight(27)
        row = row_for(header, (0, 0, 0, 0))
        row.addWidget(label(title, "section"))
        row.addStretch(1)
        row.addWidget(detail, 2)
        layout.addWidget(header)
        preview.setAccessibleName(title)
        layout.addWidget(preview, 1)
        return panel

    def build_progress(self):
        self.progress_frame = QFrame()
        self.progress_frame.setObjectName("progress")
        self.progress_frame.setFixedHeight(103)
        row = row_for(self.progress_frame)
        group = QWidget()
        column = QVBoxLayout(group)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(5)
        column.addWidget(label("Ход выполнения", "stripTitle"))
        self.done_label = label("", "secondary")
        column.addWidget(self.done_label)
        row.addWidget(group, 0, Qt.AlignmentFlag.AlignVCenter)

        self.meter = StripMeter()
        self.io_meter = QProgressBar()
        self.io_meter.setMaximumHeight(16)
        self.io_meter.setRange(0, 0)
        self.io_meter.setTextVisible(False)
        self.io_meter.setAccessibleName("Выполняется файловая операция")
        self.meters = QStackedWidget()
        self.meters.addWidget(self.meter)
        io_page = QWidget()
        row_for(io_page, (0, 0, 0, 0)).addWidget(self.io_meter)
        self.meters.addWidget(io_page)
        row.addWidget(self.meters, 1)
        self.percent_label = label("—", "percent")
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percent_label.setFixedWidth(61)
        row.addWidget(self.percent_label)
        self.threads = ThreadIndicators()
        row.addWidget(self.threads, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cancel_button = button("Отменить")
        self.cancel_button.clicked.connect(self.cancel_operation)
        row.addWidget(self.cancel_button)
        self.journal_button = button("Журнал", "journalButton")
        self.journal_button.setCheckable(True)
        self.journal_button.toggled.connect(self.toggle_journal)
        row.addWidget(self.journal_button)
        return self.progress_frame

    def build_benchmarks(self):
        page = QWidget()
        page.setObjectName("benchPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(FRAME_MARGIN, 24, FRAME_MARGIN, 24)
        layout.setSpacing(16)
        header = QWidget()
        row = row_for(header, (0, 0, 0, 0))
        row.addWidget(label("Замеры", "title"))
        row.addStretch()
        self.bench_file = label("", "secondary")
        self.bench_file.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bench_file.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.bench_file, 2)
        self.bench_cancel = button("Отменить серию")
        self.bench_cancel.clicked.connect(self.cancel_operation)
        row.addWidget(self.bench_cancel)
        self.bench_start = button("Запустить серию измерений", "primary")
        self.bench_start.clicked.connect(self.start_benchmark)
        row.addWidget(self.bench_start)
        layout.addWidget(header)
        self.bench_hint = label("Замеры ещё не выполнялись", "secondary")
        layout.addWidget(self.bench_hint)
        self.bench_table = QTableWidget(len(WORKER_COUNTS), 4)
        self.bench_table.setHorizontalHeaderLabels(
            ["Потоков", "Медиана, мс", "Ускорение", "Статус"]
        )
        self.bench_table.verticalHeader().hide()
        self.bench_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.bench_table.verticalHeader().setDefaultSectionSize(53)
        self.bench_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bench_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.bench_table.setFixedHeight(265)
        layout.addWidget(self.bench_table)
        layout.addWidget(label(f"Медиана {REPETITIONS} прогонов · без чтения и записи BMP", "secondary"))
        layout.addStretch()
        self.clear_benchmarks()
        return page

    def build_status(self):
        self.status_row = QFrame()
        self.status_row.setObjectName("statusRow")
        self.status_row.setFixedHeight(32)
        row = row_for(self.status_row)
        self.format_label = label("BMP RGB24 · обработка на Rust", "metadata")
        row.addWidget(self.format_label)
        self.status = label("Выберите исходный BMP", "status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.status, 1)
        return self.status_row

    def build_journal(self, shell):
        self.journal = QFrame(shell)
        self.journal.setObjectName("journal")
        layout = QVBoxLayout(self.journal)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(6)
        header = QWidget()
        row = row_for(header, (0, 0, 0, 0))
        row.addWidget(label("Журнал выполнения", "section"))
        row.addStretch()
        close = button("Свернуть", "textAction")
        close.clicked.connect(lambda: self.journal_button.setChecked(False))
        row.addWidget(close)
        layout.addWidget(header)
        self.log_text = QPlainTextEdit()
        self.log_text.setObjectName("logContent")
        self.log_text.setAccessibleName("События конвейера")
        self.log_text.setMaximumBlockCount(500)
        self.log_text.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("События появятся при открытии BMP или обработке.")
        layout.addWidget(self.log_text, 1)
        self.journal.hide()

    def bind_shortcuts(self):
        actions = {
            "Ctrl+O": self.open_bmp,
            "Ctrl+Return": self.start_processing,
            "Ctrl+Enter": self.start_processing,
            "Ctrl+S": self.save_result,
            "Ctrl+L": self.journal_button.toggle,
            "F1": self.show_method,
        }
        for keys, action in actions.items():
            QShortcut(QKeySequence(keys), self).activated.connect(action)

    def show_method(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("О методе")
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(label("Тиснение · ядро 3×3", "section"))
        matrix = "\n".join("   ".join(f"{value:>2}" for value in line) for line in KERNEL)
        kernel = label(matrix, "kernel")
        kernel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(kernel)
        for paragraph in (
            "Фильтр переводит цвет в яркость, применяет маску и добавляет 128. "
            "За краем изображения повторяется ближайший пиксель.",
            "Рабочие потоки берут полосы из одной очереди. У каждой полосы свой выходной "
            "буфер, а соседние строки читаются из общего исходника.",
            "Mutex защищает очередь, готовые полосы и счётчик. Barrier согласует старт. "
            "Condvar будит рабочих при появлении заданий или отмене.",
        ):
            text = label(paragraph, "secondary")
            text.setWordWrap(True)
            layout.addWidget(text)
        finish = button("Закрыть")
        finish.clicked.connect(dialog.accept)
        layout.addWidget(finish, 0, Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def change_page(self, index: int):
        self.pages.setCurrentIndex(index)
        if self.journal.isVisible():
            self.place_journal()

    def toggle_journal(self, shown: bool):
        self.journal.setVisible(shown)
        if shown:
            self.place_journal()
            self.journal.raise_()

    def place_journal(self):
        shell = self.centralWidget()
        workspace = self.pages.currentIndex() == 0
        anchor = self.progress_frame if workspace else self.status_row
        bottom = anchor.mapTo(shell, QPoint(0, 0)).y()
        top = self.gallery.mapTo(shell, QPoint(0, 0)).y() if workspace else 0
        height = min(244, bottom - top - 14)
        self.journal.setGeometry(
            FRAME_MARGIN, bottom - height - 8, shell.width() - 2 * FRAME_MARGIN, height
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toolbar_row"):
            compact = self.width() < 1080
            self.toolbar_row.setSpacing(8 if compact else 16)
            self.primary.setFixedWidth(190 if compact else 224)
            self.repeat_button.setText("Повторить" if compact else "Обработать снова")
            gallery_margin = 10 if self.height() < 620 else 18
            self.gallery_row.setContentsMargins(FRAME_MARGIN, gallery_margin, FRAME_MARGIN, gallery_margin)
        if hasattr(self, "journal") and self.journal.isVisible():
            self.place_journal()

    def log(self, source: str, message: str, timestamp: datetime | None = None):
        moment = timestamp or datetime.now()
        time_text = moment.strftime("%H:%M:%S.%f")[:-3]
        scrollbar = self.log_text.verticalScrollBar()
        position = scrollbar.value()
        at_end = position >= scrollbar.maximum()
        self.log_text.appendPlainText(f"{time_text}   [{source}]   {message}")
        scrollbar.setValue(scrollbar.maximum() if at_end else position)

    def set_status(self, message: str, tone: str = ""):
        self.status.setText(message)
        self.status.setToolTip(message)
        if self.status.property("tone") != tone:
            self.status.setProperty("tone", tone)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)

    def refresh_controls(self):
        kind = self.job.kind if self.job else None
        busy = kind is not None
        self.open_button.setEnabled(not busy)
        self.segments.setEnabled(not busy and self.source is not None)
        self.repeat_button.setVisible(self.result is not None and not busy)
        self.primary.setEnabled(not busy and self.source is not None)
        self.primary.setText(JOB_TITLES.get(
            kind, "Сохранить BMP" if self.result is not None else "Начать обработку"
        ))
        cancellable = busy and not self.cancelling and not self.closing
        self.cancel_button.setEnabled(cancellable)
        self.cancel_button.setText("Отмена…" if self.cancelling else "Отменить")
        self.bench_cancel.setVisible(kind == "benchmark")
        self.bench_cancel.setEnabled(cancellable)
        self.bench_start.setEnabled(not busy and self.source is not None)
        self.meters.setCurrentIndex(int(kind in ("read", "save")))
        self.percent_label.setVisible(kind not in ("read", "save"))

    def set_progress(self, strips: list[str], done: int):
        total = len(strips)
        self.meter.set_strips(strips)
        self.done_label.setText(f"{done} из {total} фрагментов" if total else "Нет активной обработки")
        if total:
            percent = 100 * done // total
            self.percent_label.setText(f"{percent}%")
            self.percent_label.setAccessibleName(f"Прогресс обработки — {percent} процентов")
        else:
            self.percent_label.setText("—")
            self.percent_label.setAccessibleName("Прогресс обработки недоступен")

    def reset_progress(self):
        total = min(STRIP_COUNT, self.source.height) if self.source else 0
        self.set_progress(["waiting"] * total, 0)
        self.threads.set_states([])

    def poll_pool(self):
        snapshot = self.processor.take_snapshot()
        if snapshot.strips and not self.cancelling:
            self.set_progress(snapshot.strips, snapshot.done)
        self.threads.set_states(snapshot.workers)
        if snapshot.phase != self.last_phase:
            if snapshot.phase == "assembling" and not self.cancelling:
                self.set_status("Сборка готовых полос…")
            self.last_phase = snapshot.phase
        for time_ms, source, message in snapshot.events:
            self.log(source, message, datetime.fromtimestamp(time_ms / 1000))

    def open_bmp(self):
        if self.job is not None:
            return
        directory = str(self.source_path.parent) if self.source_path else ""
        filename, _ = QFileDialog.getOpenFileName(self, "Открыть BMP", directory, "BMP (*.bmp)")
        if filename:
            self.load_path(Path(filename))

    def load_path(self, path: Path):
        if self.job is not None:
            return
        cancellation = FileCancellation()
        self.log("I/O", f"Начато чтение {path.name}")
        self.set_status(f"Чтение {path.name}…")
        self.start_job("read", lambda: (path, *read_with_preview(path, cancellation)), cancellation.cancel)

    def primary_clicked(self):
        if self.result is None:
            self.start_processing()
        else:
            self.save_result()

    def start_processing(self):
        if self.job is not None or self.source is None or self.pages.currentIndex() != 0:
            return
        self.result = None
        self.result_preview.set_image(None)
        self.ready_label.clear()
        self.reset_progress()
        self.last_phase = ""
        self.processor = Processor()
        workers = self.thread_group.checkedId()
        self.set_status("Обработка полос…")
        self.log("UI", f"Начат расчёт · размер пула {workers}")
        operation = partial(process_with_preview, self.source, self.processor, workers)
        self.start_job("process", operation, self.processor.cancel)
        self.timer.start()

    def save_result(self):
        if self.job is not None or self.result is None:
            return
        dialog = QFileDialog(self, "Сохранить BMP", str(self.source_path.parent), "BMP (*.bmp)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("bmp")
        dialog.selectFile(f"{self.source_path.stem}_emboss.bmp")
        if dialog.exec():
            self.save_path(Path(dialog.selectedFiles()[0]))

    def save_path(self, path: Path):
        if self.job is not None or self.result is None:
            return
        if path.suffix.lower() != ".bmp":
            self.set_status("Для результата нужно расширение .bmp", "error")
            return
        cancellation = FileCancellation()
        self.log("I/O", f"Начато сохранение {path.name}")
        self.set_status(f"Сохранение {path.name}…")
        self.start_job("save", partial(save_bmp, path, self.result, cancellation), cancellation.cancel)

    def clear_benchmarks(self):
        self.measurements.clear()
        self.bench_hint.setText("Замеры ещё не выполнялись")
        for row, count in enumerate(WORKER_COUNTS):
            self.set_bench_row(row, (str(count), "—", "—", "Не выполнено"))

    def set_bench_row(self, row: int, values: tuple[str, str, str, str]):
        for column, text in enumerate(values):
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.bench_table.setItem(row, column, cell)

    def start_benchmark(self):
        if self.job is not None or self.source is None:
            return
        self.clear_benchmarks()
        series = Benchmark()
        self.set_status("Выполняется серия измерений…")
        self.start_job(
            "benchmark", partial(series.run, self.source, self.benchmark_update.emit), series.cancel
        )

    @Slot(int, object)
    def update_benchmark(self, count: int, measurement: Measurement | None):
        if self.job is None or self.job.kind != "benchmark" or self.cancelling:
            return
        row = list(WORKER_COUNTS).index(count)
        if measurement is None:
            self.bench_hint.setText(f"Тест {row + 1} из {len(WORKER_COUNTS)} · потоков {count}")
            self.set_bench_row(row, (str(count), "—", "—", "Измеряется…"))
            return
        self.measurements[count] = measurement
        baseline = self.measurements[1].median_ms
        self.set_bench_row(row, (str(count), f"{measurement.median_ms:.2f}",
                                f"{baseline / measurement.median_ms:.2f}×", "Завершено"))
        samples = " · ".join(f"{value:.2f}" for value in measurement.samples_ms)
        self.bench_table.item(row, 1).setToolTip(f"Прогоны, мс — {samples}")

    def start_job(self, kind: str, operation, cancel):
        self.cancelling = False
        self.cancel_job = cancel
        self.job = BackgroundJob(kind, operation, self)
        self.job.finished.connect(self.finish_job)
        self.refresh_controls()
        self.job.start()

    def cancel_operation(self):
        if self.job is None or self.cancelling:
            return
        if self.cancel_job() is False:
            return
        self.cancelling = True
        self.set_status("Ожидание завершения операции…", "warning")
        self.refresh_controls()

    @Slot()
    def finish_job(self):
        job = self.job
        self.timer.stop()
        if job.kind == "process":
            self.poll_pool()
            self.processor = None
        cancelled = isinstance(job.error, ProcessingCancelled) or (self.cancelling and job.error is None)
        if cancelled:
            self.finish_cancelled(job.kind)
        elif job.error is not None:
            self.finish_error(job.kind, job.error)
        else:
            self.finish_success(job.kind, job.value)
        self.job = None
        self.cancel_job = None
        self.cancelling = False
        job.deleteLater()
        self.refresh_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)

    def finish_success(self, kind: str, value):
        if kind == "read":
            self.source_path, self.source, preview = value
            self.result = None
            self.original_preview.set_image(preview)
            self.result_preview.set_image(None)
            self.ready_label.clear()
            self.file_label.setText(self.source_path.name)
            self.file_label.setToolTip(str(self.source_path))
            size = f"{self.source.width} × {self.source.height}"
            self.format_label.setText(f"BMP RGB24 · {size} · обработка на Rust")
            self.bench_file.setText(f"{self.source_path.name} · {size}")
            self.bench_file.setToolTip(str(self.source_path))
            self.clear_benchmarks()
            self.reset_progress()
            self.set_status("Исходное изображение готово")
            self.log("I/O", f"Прочитан {self.source_path.name} · {size}")
        elif kind == "process":
            self.result, preview = value
            self.result_preview.set_image(preview)
            self.ready_label.setText("Готово")
            self.set_status("Обработка завершена", "success")
        elif kind == "save":
            self.set_status("BMP сохранён", "success")
            self.log("I/O", "Результат записан, временный файл опубликован")
        elif kind == "benchmark":
            self.bench_hint.setText("Серия завершена")
            self.set_status("Замеры завершены", "success")
            self.log("UI", "Измерены режимы с 1, 2, 4 и 8 потоками")

    def finish_cancelled(self, kind: str):
        messages = {
            "read": "Чтение отменено",
            "process": "Обработка отменена. Неполный результат не сохранён.",
            "save": "Сохранение отменено",
            "benchmark": "Серия измерений отменена",
        }
        if kind == "process":
            self.reset_progress()
        if kind == "benchmark":
            self.finish_partial_benchmark(messages[kind])
        self.set_status(messages[kind], "warning")
        self.log("UI", messages[kind])

    def finish_error(self, kind: str, error: Exception):
        fallback = {
            "read": "Не удалось прочитать изображение",
            "process": "Обработка прервана из-за ошибки",
            "save": "Не удалось сохранить BMP",
            "benchmark": "Ошибка серии измерений",
        }
        message = str(error) or fallback[kind]
        if isinstance(error, MemoryError):
            message = "Не хватает памяти. Выберите изображение меньшего размера."
        if kind == "process":
            self.reset_progress()
        if kind == "benchmark":
            self.finish_partial_benchmark(fallback[kind])
        self.set_status(message, "error")
        details = f" · {error.__cause__}" if error.__cause__ else ""
        self.log("Ошибка", f"{message}{details}")

    def finish_partial_benchmark(self, message: str):
        self.bench_hint.setText(message)
        for row, count in enumerate(WORKER_COUNTS):
            if count not in self.measurements:
                self.set_bench_row(row, (str(count), "—", "—", "Не выполнено"))

    def closeEvent(self, event):
        if self.job is None:
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            self.cancel_operation()
            self.refresh_controls()

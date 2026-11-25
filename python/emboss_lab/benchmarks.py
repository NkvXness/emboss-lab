import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from threading import Lock
from time import perf_counter

from ._native import ProcessingCancelled, Processor, WORKER_COUNTS
from .image_io import BmpError, RgbImage, load_bmp

REPETITIONS = 5


@dataclass(frozen=True)
class Measurement:
    workers: int
    samples_ms: tuple[float, ...]

    @property
    def median_ms(self) -> float:
        return median(self.samples_ms)


class Benchmark:
    def __init__(self):
        self._lock = Lock()
        self._current = None
        self._cancelled = False

    def cancel(self):
        with self._lock:
            self._cancelled = True
            if self._current is not None:
                self._current.cancel()

    def _measure(self, image: RgbImage, workers: int) -> float:
        processor = Processor()
        with self._lock:
            if self._cancelled:
                raise ProcessingCancelled("Серия измерений отменена")
            self._current = processor
        try:
            start = perf_counter()
            result = processor.process(image.pixels, image.width, image.height, workers)
            elapsed = perf_counter() - start
            del result
        finally:
            with self._lock:
                self._current = None
        return elapsed * 1000

    def run(self, image: RgbImage, update: Callable[[int, Measurement | None], None]):
        for workers in WORKER_COUNTS:
            update(workers, None)
            self._measure(image, workers)  # Прогрев вне статистики для каждого размера пула.
            samples = tuple(self._measure(image, workers) for _ in range(REPETITIONS))
            with self._lock:
                if self._cancelled:
                    raise ProcessingCancelled("Серия измерений отменена")
            update(workers, Measurement(workers, samples))


def main():
    parser = argparse.ArgumentParser(description="Измерение времени работы Rust-пула")
    parser.add_argument("bmp", type=Path)
    path = parser.parse_args().bmp
    try:
        image = load_bmp(path)
    except (BmpError, MemoryError, OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"{path.name} · {image.width} × {image.height} · {REPETITIONS} прогонов")
    print("Потоки | Прогоны, мс | Медиана, мс | Ускорение")
    baseline = None

    def report(_workers, measurement):
        nonlocal baseline
        if measurement is None:
            return
        if baseline is None:
            baseline = measurement.median_ms
        samples = " / ".join(f"{sample:.3f}" for sample in measurement.samples_ms)
        print(f"{measurement.workers} | {samples} | {measurement.median_ms:.3f} | "
              f"{baseline / measurement.median_ms:.3f}×")

    Benchmark().run(image, report)


if __name__ == "__main__":
    main()

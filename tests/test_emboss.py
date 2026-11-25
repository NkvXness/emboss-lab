from random import Random
from threading import Thread
from time import monotonic, sleep

import pytest

from emboss_lab._native import ProcessingCancelled, Processor, STRIP_COUNT, WORKER_COUNTS


def reference_emboss(rgb: bytes, width: int, height: int) -> bytes:
    mask = ((-2, -1, 0), (-1, 0, 1), (0, 1, 2))
    gray = [
        (299 * r + 587 * g + 114 * b + 500) // 1000
        for r, g, b in zip(rgb[::3], rgb[1::3], rgb[2::3])
    ]
    output = bytearray()
    for y in range(height):
        for x in range(width):
            value = 128
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    row = max(0, min(height - 1, y + dy))
                    col = max(0, min(width - 1, x + dx))
                    value += mask[dy + 1][dx + 1] * gray[row * width + col]
            output.extend([max(0, min(255, value))] * 3)
    return bytes(output)


def test_3_by_3_known_gray_values():
    data = bytes(value for v in range(0, 36, 4) for value in [v] * 3)
    expected = [176, 188, 176, 212, 224, 212, 176, 188, 176]
    assert Processor().process(data, 3, 3, 4) == bytes(v for v in expected for _ in range(3))


@pytest.mark.parametrize("width,height", [(1, 1), (1, 43), (77, 1), (5, 5), (83, 71)])
def test_reference_and_all_pool_sizes(width, height):
    rgb = Random(11).randbytes(width * height * 3)
    expected = reference_emboss(rgb, width, height)
    for workers in WORKER_COUNTS:
        processor = Processor()
        assert processor.process(rgb, width, height, workers) == expected
        progress = processor.take_snapshot()
        assert progress.phase == "completed"
        assert progress.done == min(STRIP_COUNT, height)
        assert progress.strips == ["done"] * progress.done
        assert progress.workers == ["done"] * workers
        assert sum("Взял полосу" in event for _, _, event in progress.events) == progress.done


def test_black_white_step_and_saturation():
    rgb = bytes([255] * 6 + [0] * 6) * 37
    result = Processor().process(rgb, 4, 37, 4)
    assert result == bytes([128] * 3 + [0] * 6 + [128] * 3) * 37


def test_cancel_before_start_and_run_again():
    processor = Processor()
    processor.cancel()
    with pytest.raises(ProcessingCancelled):
        processor.process(bytes(27), 3, 3, 8)
    assert processor.take_snapshot().phase == "cancelled"
    assert Processor().process(bytes(27), 3, 3, 4) == bytes([128] * 27)


@pytest.mark.parametrize("target", [8, 29])
def test_cancel_a_live_pool(target):
    size = 4096
    rgb = bytes(size * size * 3)
    processor = Processor()
    outcomes = []

    def run():
        try:
            outcomes.append(processor.process(rgb, size, size, 1))
        except Exception as error:
            outcomes.append(error)

    worker = Thread(target=run, daemon=True)
    worker.start()
    last_done = 0
    deadline = monotonic() + 10
    while worker.is_alive() and monotonic() < deadline:
        state = processor.take_snapshot()
        assert last_done <= state.done <= STRIP_COUNT
        last_done = state.done
        if target <= state.done < STRIP_COUNT:
            processor.cancel()
            break
        sleep(0.0005)
    worker.join(10)
    assert not worker.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], ProcessingCancelled)
    final = processor.take_snapshot()
    assert final.phase == "cancelled"
    assert final.workers == ["stopped"]


def test_repeat_without_losing_or_reordering_strips():
    width, height = 70, 59
    data = Random(57).randbytes(width * height * 3)
    expected = reference_emboss(data, width, height)
    for count in WORKER_COUNTS * 10:
        assert Processor().process(data, width, height, count) == expected


@pytest.mark.parametrize("width,height,workers", [(0, 1, 4), (2, 1, 4), (1, 1, 3), (99999, 1, 1)])
def test_invalid_native_input(width, height, workers):
    with pytest.raises(ValueError):
        Processor().process(bytes(3), width, height, workers)

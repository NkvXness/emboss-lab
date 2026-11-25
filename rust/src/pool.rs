use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Barrier, Condvar, Mutex, MutexGuard};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::emboss::apply_emboss_to_strip;

pub const STRIP_COUNT: usize = 32; // Несколько заданий на рабочего выравнивают нагрузку.
pub const WORKER_COUNTS: [usize; 4] = [1, 2, 4, 8];
pub const MAX_DIMENSION: usize = 16_384;
pub const MAX_PIXELS: usize = 24_000_000;
const EVENT_LIMIT: usize = 128; // Ограничение буфера событий между опросами окна.

#[derive(Debug, PartialEq, Eq)]
pub enum Failure {
    Input(&'static str),
    Cancelled,
    Memory,
    Pool(String),
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
enum Phase {
    #[default]
    Idle,
    Creating,   // Рабочие ждут подтверждения, что весь пул создан.
    Starting,   // Создание пула подтверждено, очередь ещё не опубликована.
    Processing, // В этой фазе очередь уже не пополняется.
    Assembling,
    Completed,
    Cancelled,
    Failed,
}

impl Phase {
    fn name(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Creating | Self::Starting => "starting",
            Self::Processing => "processing",
            Self::Assembling => "assembling",
            Self::Completed => "completed",
            Self::Cancelled => "cancelled",
            Self::Failed => "failed",
        }
    }
}

struct Strip {
    index: usize,
    rows: std::ops::Range<usize>, // Строка с индексом конца диапазона не входит в полосу.
}

// index  — номер полосы, считая с нуля.
// height — высота изображения в строках пикселей.
// total  — общее число полос.
fn split_into_strips(height: usize) -> VecDeque<Strip> {
    let total = height.min(STRIP_COUNT); // Не создаём полосы без строк.
    (0..total) // Конец каждой полосы совпадает с началом следующей.
        .map(|index| Strip {
            index,
            rows: index * height / total..(index + 1) * height / total,
        })
        .collect()
}

pub type Event = (u64, String, String); // Метка времени Unix в мс, источник, сообщение.

#[derive(Default)]
struct State {
    phase: Phase,
    queue: VecDeque<Strip>,
    results: Vec<Option<Vec<u8>>>,
    strips: Vec<&'static str>,
    workers: Vec<&'static str>,
    done: usize,
    events: VecDeque<Event>,
}

impl State {
    fn log(&mut self, source: String, message: String) {
        let time = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        if self.events.len() == EVENT_LIMIT {
            self.events.pop_front(); // При переполнении оставляем свежие события.
        }
        self.events.push_back((time, source, message));
    }
}

#[derive(Default)]
pub struct Pool {
    state: Mutex<State>,   // Защищает очередь, результаты, статусы и журнал.
    changed: Condvar,      // Будит рабочих при смене состояния или отмене.
    cancelled: AtomicBool, // Фильтр читает флаг без захвата мьютекса.
}

pub struct Snapshot {
    pub phase: &'static str,
    pub done: usize,
    pub strips: Vec<&'static str>,
    pub workers: Vec<&'static str>,
    pub events: Vec<Event>,
}

impl Pool {
    fn state(&self) -> MutexGuard<'_, State> {
        // При отравлении мьютекса забираем guard, чтобы завершить работу пула.
        self.state.lock().unwrap_or_else(|error| error.into_inner())
    }

    fn wait<'a>(&self, state: MutexGuard<'a, State>) -> MutexGuard<'a, State> {
        self.changed
            .wait(state)
            .unwrap_or_else(|error| error.into_inner())
    }

    pub fn snapshot(&self) -> Snapshot {
        let mut state = self.state(); // Снимок согласован по всем полям.
        Snapshot {
            phase: state.phase.name(),
            done: state.done,
            strips: state.strips.clone(),
            workers: state.workers.clone(),
            events: state.events.drain(..).collect(), // Следующий опрос получит новые события.
        }
    }

    pub fn cancel(&self) {
        let mut state = self.state();
        if matches!(
            state.phase,
            Phase::Completed | Phase::Cancelled | Phase::Failed
        ) {
            return;
        }
        // Флаг меняется под мьютексом очереди, чтобы рабочий не пропустил отмену перед сном.
        if !self.cancelled.swap(true, Ordering::Relaxed) {
            state.log("Пул".into(), "Запрошена отмена".into()); // Только первый запрос.
            self.changed.notify_all();
        }
    }

    // Проверка входных данных
    pub fn process_image(
        &self,
        rgb: &[u8], // Плотный буфер RGB, по три байта на пиксель.
        width: usize,
        height: usize,
        workers: usize,
    ) -> Result<Vec<u8>, Failure> {
        if !WORKER_COUNTS.contains(&workers) {
            return Err(Failure::Input("Допустимо 1, 2, 4 или 8 потоков"));
        }
        if width == 0
            || height == 0
            || width > MAX_DIMENSION
            || height > MAX_DIMENSION
            || width * height > MAX_PIXELS
        {
            return Err(Failure::Input("Неподдерживаемый размер изображения"));
        }
        // Расчёт размера буфера
        if rgb.len() != width * height * 3 {
            return Err(Failure::Input("Длина RGB-буфера не соответствует размерам"));
        }
        {
            let mut state = self.state();
            if state.phase != Phase::Idle {
                return Err(Failure::Input("Для нового запуска нужен новый Processor"));
            }
            state.phase = Phase::Creating; // Резервируем экземпляр пула за текущим запуском.
            let total = height.min(STRIP_COUNT);
            state.strips = vec!["waiting"; total];
            state.results.resize_with(total, || None); // Пустые ячейки до публикации полос.
        }

        let result = self
            // После проверки данных запускаем рабочие потоки
            .run_pool(rgb, width, height, workers)
            .and_then(|()| self.assemble(rgb.len())); // Только после успешного завершения пула.

        let (result, leftovers) = {
            // Захват мьютекса
            let mut state = self.state();
            // Мьютекс согласует последнюю проверку отмены с публикацией результата.
            let result = match result {
                Ok(_) if self.cancelled.load(Ordering::Relaxed) => Err(Failure::Cancelled),
                result => result,
            };
            match &result {
                Ok(_) => {
                    state.phase = Phase::Completed;
                    state.log("Пул".into(), "Сборка результата завершена".into());
                }
                Err(Failure::Cancelled) => {
                    state.phase = Phase::Cancelled;
                    state.log("Пул".into(), "Обработка отменена, потоки завершены".into());
                }
                Err(error) => {
                    state.phase = Phase::Failed;
                    state.log("Пул".into(), format!("Ошибка: {error}"));
                }
            }
            state.queue.clear();
            (result, std::mem::take(&mut state.results))
        };
        drop(leftovers); // Освобождаем буферы после выхода из критической секции.
        result
    }

    fn run_pool(
        &self,
        rgb: &[u8], // Рабочие заимствуют исходник в пределах thread::scope.
        width: usize,
        height: usize,
        workers: usize,
    ) -> Result<(), Failure> {
        if self.cancelled.load(Ordering::Relaxed) {
            return Err(Failure::Cancelled);
        }
        let barrier = Barrier::new(workers + 1); // Дополнительный участник — планировщик.
        let barrier = &barrier;
        // Управляющий поток
        thread::scope(|scope| {
            let mut handles = Vec::with_capacity(workers);
            let mut failure = None;
            // Управляющий поток проходит столько раз, сколько рабочих выбрали в окне
            for id in 0..workers {
                let worker = thread::Builder::new().name(format!("T{}", id + 1));
                match worker.spawn_scoped(scope, move || self.work(id, barrier, rgb, width, height))
                {
                    Ok(handle) => handles.push(handle),
                    Err(error) => {
                        failure = Some(Failure::Pool(format!("Не удалось создать поток: {error}")));
                        break;
                    }
                }
            }
            {
                let mut state = self.state();
                state.workers = vec!["waiting"; handles.len()];
                // При сбое созданные рабочие выходят до барьера, который иначе не заполнится.
                state.phase = if failure.is_some() {
                    Phase::Failed
                } else {
                    Phase::Starting
                };
                if failure.is_none() {
                    state.log("Пул".into(), format!("Создано рабочих потоков: {workers}"));
                } else {
                    state.workers.fill("stopped");
                }
                // Управляющий закончил создавать потоки, он записывает, удалось ли это, и будит ожидающих
                self.changed.notify_all();
            }
            if failure.is_none() {
                // Барьер синхронизирует готовность всех участников перед выдачей заданий.
                barrier.wait();
                // Деление изображения на полосы
                let strips = split_into_strips(height);
                let mut state = self.state();
                if !self.cancelled.load(Ordering::Relaxed) {
                    state.log(
                        "Пул".into(),
                        format!("Барьер пройден, полос в очереди: {}", strips.len()),
                    );
                    state.queue = strips;
                }
                // После перехода в Processing планировщик больше не наполняет очередь.
                state.phase = Phase::Processing;
                self.changed.notify_all(); // Мьютекс отпустится в конце блока перед join.
            }
            for handle in handles {
                let worker_error = match handle.join() {
                    Ok(result) => result.err(), // Ошибка вычисления при штатном выходе.
                    Err(_) => Some(Failure::Pool("Сбой рабочего потока".into())),
                };
                if failure.is_none() {
                    failure = worker_error; // Сохраняем первую обнаруженную ошибку.
                }
            }
            if let Some(error) = failure {
                return Err(error);
            }
            self.state().log(
                "Пул".into(),
                "Все рабочие потоки присоединены (join)".into(),
            );
            Ok(())
        })
    }

    // ┌─ WORK · ЦИКЛ ОДНОГО РАБОЧЕГО ПОТОКА
    // │
    // │  1. Ждёт готовности пула и проходит стартовый барьер.
    // │  2. Берёт полосу из общей очереди и считает её вне мьютекса.
    // │  3. Под мьютексом сохраняет полосу по её номеру,
    // │     увеличивает счётчик готовых полос и пишет в журнал.
    // │  4. Возвращается к очереди за следующей полосой.
    // │
    // │  Выходит, когда все задания разобраны или запрошена отмена.
    // │  При ошибке расчёта возвращает её управляющему потоку.
    // └──────────────────────────────────────────────────────────
    fn work(
        &self,
        id: usize,
        barrier: &Barrier,
        rgb: &[u8],
        width: usize,
        height: usize,
    ) -> Result<(), Failure> {
        {
            // Захват мьютекса, позволяем только одному потоку за раз работать с общими данными
            let mut state = self.state();
            while state.phase == Phase::Creating {
                state = self.wait(state);
            }
            if state.phase == Phase::Failed {
                return Ok(());
            }
        }
        barrier.wait(); // К барьеру подходим с отпущенной блокировкой.
        let name = format!("T{}", id + 1);
        while let Some(strip) = self.next_strip(id, &name) {
            // Передаём диапазон строк для рассчёта
            let output = apply_emboss_to_strip(rgb, width, height, strip.rows, &self.cancelled);
            let mut state = self.state();
            match output {
                Ok(pixels)
                    if !self.cancelled.load(Ordering::Relaxed) && state.phase != Phase::Failed =>
                {
                    state.results[strip.index] = Some(pixels); // Индекс задаёт порядок сборки.
                    state.strips[strip.index] = "done";
                    state.done += 1;
                    let done = state.done;
                    state.log(
                        name.clone(),
                        format!("Завершил полосу {} (готово {done})", strip.index + 1),
                    );
                }
                Ok(_) | Err(Failure::Cancelled) => {
                    state.strips[strip.index] = "stopped";
                    state.workers[id] = "stopped";
                    return Ok(()); // Причина остановки уже отражена в состоянии пула.
                }
                Err(error) => {
                    state.strips[strip.index] = "stopped";
                    state.workers[id] = "stopped";
                    state.phase = Phase::Failed; // Остальные рабочие перестанут брать задания.
                    state.log(
                        name.clone(),
                        format!("Ошибка при обработке полосы {}: {error}", strip.index + 1),
                    );
                    self.changed.notify_all();
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    // Возвращает одну полосу или None, если рабочему пора завершиться.
    fn next_strip(&self, id: usize, name: &str) -> Option<Strip> {
        let mut state = self.state();
        // Выдачу, смену статусов и запись в журнал защищает один мьютекс.
        loop {
            if self.cancelled.load(Ordering::Relaxed) || state.phase == Phase::Failed {
                state.workers[id] = "stopped";
                return None; // Отмена или сбой останавливают выдачу заданий.
            }
            if let Some(strip) = state.queue.pop_front() {
                state.workers[id] = "working"; // Эта полоса уже удалена из общей очереди.
                state.strips[strip.index] = "working";
                state.log(name.into(), format!("Взял полосу {}", strip.index + 1));
                return Some(strip); // При возврате мьютекс освобождается.
            }
            if state.phase == Phase::Processing {
                state.workers[id] = "done"; // Пакет уже выдан, очередь больше не пополнится.
                return None;
            }
            state.workers[id] = "waiting"; // Планировщик ещё не опубликовал очередь.
            state = self.wait(state); // Отпускаем мьютекс на время сна и повторяем проверки.
        }
    }

    // Соединяет готовые полосы в один массив байтов всей картинки
    fn assemble(&self, size: usize) -> Result<Vec<u8>, Failure> {
        let results = {
            let mut state = self.state();
            if self.cancelled.load(Ordering::Relaxed) {
                return Err(Failure::Cancelled);
            }
            state.phase = Phase::Assembling;
            state.log("Пул".into(), "Начата сборка полос по порядку".into());
            std::mem::take(&mut state.results) // Забираем буферы без копирования пикселей.
        };
        let mut output = Vec::new();
        output
            .try_reserve_exact(size)
            .map_err(|_| Failure::Memory)?;
        for strip in results {
            if self.cancelled.load(Ordering::Relaxed) {
                return Err(Failure::Cancelled);
            }
            let pixels =
                strip.ok_or_else(|| Failure::Pool("В результате отсутствует полоса".into()))?;
            output.extend_from_slice(&pixels); // Обход сохраняет порядок строк исходника.
        }
        Ok(output)
    }
}

impl std::fmt::Display for Failure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Input(message) => formatter.write_str(message), // Текст проверки ввода.
            Self::Cancelled => formatter.write_str("Обработка отменена"),
            Self::Memory => formatter.write_str("Не хватает памяти для обработки"),
            Self::Pool(message) => formatter.write_str(message),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_cover_image_with_no_overlap() {
        for height in [1, 6, 32, 33, 1080] {
            let strips = split_into_strips(height);
            assert_eq!(strips.len(), height.min(STRIP_COUNT));
            let rows: Vec<_> = strips.into_iter().flat_map(|strip| strip.rows).collect();
            assert_eq!(rows, (0..height).collect::<Vec<_>>()); // Без пропусков и повторов.
        }
    }

    #[test]
    fn uniform_pixels_and_more_workers_than_jobs() {
        let input = [10, 20, 30].repeat(2); // Две одноцветные строки на восемь рабочих.
        let pool = Pool::default();
        assert_eq!(pool.process_image(&input, 1, 2, 8).unwrap(), vec![128; 6]);
        let snapshot = pool.snapshot();
        assert_eq!(snapshot.done, 2);
        assert_eq!(snapshot.workers, vec!["done"; 8]);
        assert_eq!(snapshot.phase, "completed");
    }

    #[test]
    fn cancellation_before_processing_or_assembly() {
        let pool = Pool::default();
        pool.cancel(); // Запрос допустим, пока пул ещё в фазе Idle.
        assert_eq!(
            pool.process_image(&[0; 3], 1, 1, 4),
            Err(Failure::Cancelled)
        );
        assert_eq!(pool.snapshot().phase, "cancelled");

        let pool = Pool::default(); // Состояние перед сборкой готовим вручную.
        {
            let mut state = pool.state();
            state.phase = Phase::Processing;
            state.results.push(Some(vec![128; 3]));
            state.done = 1;
        }
        pool.cancel();
        assert_eq!(pool.assemble(3), Err(Failure::Cancelled));
    }
}

mod emboss;
mod pool;

use pool::{Event, Failure, Pool};
use pyo3::exceptions::{PyException, PyMemoryError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

pyo3::create_exception!(_native, ProcessingCancelled, PyException);

#[pyclass(module = "emboss_lab._native", frozen)]
#[derive(Default)]
struct Processor {
    pool: Pool,
}

#[pyclass(module = "emboss_lab._native", get_all, frozen)]
struct Snapshot {
    phase: &'static str,
    done: usize,
    strips: Vec<&'static str>,
    workers: Vec<&'static str>,
    events: Vec<Event>,
}

#[pymethods]
impl Processor {
    #[new]
    fn new() -> Self {
        Self::default()
    }

    fn process<'py>(
        &self,
        py: Python<'py>,
        rgb: &Bound<'py, PyBytes>,
        width: usize,
        height: usize,
        workers: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        // Даём Rust доступ к исходным байтам
        let input = rgb.as_bytes();
        // Python bytes неизменяемы и живут до возврата. Рабочим доступен только срез.
        let output = py.detach(|| self.pool.process_image(input, width, height, workers));
        let output = output.map_err(|error| match error {
            Failure::Cancelled => ProcessingCancelled::new_err(error.to_string()),
            Failure::Memory => PyMemoryError::new_err(error.to_string()),
            Failure::Input(message) => PyValueError::new_err(message),
            Failure::Pool(message) => PyRuntimeError::new_err(message),
        })?;
        // Из готовых байтов создаётся объект Python, который вернётся в workers.py
        PyBytes::new_with(py, output.len(), |buffer| {
            buffer.copy_from_slice(&output);
            Ok(())
        })
    }

    fn take_snapshot(&self) -> Snapshot {
        let state = self.pool.snapshot();
        Snapshot {
            phase: state.phase,
            done: state.done,
            strips: state.strips,
            workers: state.workers,
            events: state.events,
        }
    }

    fn cancel(&self) {
        self.pool.cancel();
    }
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<Processor>()?;
    module.add_class::<Snapshot>()?;
    module.add(
        "ProcessingCancelled",
        module.py().get_type::<ProcessingCancelled>(),
    )?;
    module.add("MAX_DIMENSION", pool::MAX_DIMENSION)?;
    module.add("MAX_PIXELS", pool::MAX_PIXELS)?;
    module.add("STRIP_COUNT", pool::STRIP_COUNT)?;
    module.add("WORKER_COUNTS", pool::WORKER_COUNTS)?;
    module.add("KERNEL", emboss::KERNEL)?;
    Ok(())
}

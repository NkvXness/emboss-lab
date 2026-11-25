use std::sync::atomic::{AtomicBool, Ordering};

use crate::pool::Failure;

// Таблица весов 3 × 3 для фильтра тиснения.
// Когда программа считает один пиксель, она накладывает эту таблицу на квадрат вокруг него. Текущий пиксель находится в центре.
pub const KERNEL: [[i32; 3]; 3] = [[-2, -1, 0], [-1, 0, 1], [0, 1, 2]];

fn luminance(rgb: &[u8], offset: usize) -> i32 {
    let r = i32::from(rgb[offset]);
    let g = i32::from(rgb[offset + 1]);
    let b = i32::from(rgb[offset + 2]);
    (299 * r + 587 * g + 114 * b + 500) / 1000
}

pub fn apply_emboss_to_strip(
    rgb: &[u8],
    width: usize,
    height: usize,
    rows: std::ops::Range<usize>,
    cancelled: &AtomicBool,
) -> Result<Vec<u8>, Failure> {
    let mut output = Vec::new();
    output
        .try_reserve_exact(rows.len() * width * 3)
        .map_err(|_| Failure::Memory)?;

    for y in rows {
        if cancelled.load(Ordering::Relaxed) {
            return Err(Failure::Cancelled);
        }
        // Соседи берутся из всего исходника, в том числе за границей полосы.
        let source_rows = [y.saturating_sub(1), y, (y + 1).min(height - 1)];
        for x in 0..width {
            let source_columns = [x.saturating_sub(1), x, (x + 1).min(width - 1)];
            let mut value = 128;
            for (row, weights) in source_rows.iter().zip(KERNEL) {
                for (column, weight) in source_columns.iter().zip(weights) {
                    if weight != 0 {
                        let offset = (row * width + column) * 3;
                        value += weight * luminance(rgb, offset);
                    }
                }
            }
            output.extend_from_slice(&[value.clamp(0, 255) as u8; 3]);
        }
    }
    Ok(output)
}

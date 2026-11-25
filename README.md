# EMBOSS LAB

Лабораторная работа № 4, вариант 11. Приложение для Windows 11 применяет фильтр тиснения к изображению, показывает работу пула потоков и сохраняет результат в BMP.

## Запуск

Эта команда PowerShell запускает приложение из папки проекта с уже собранным окружением.

```powershell
.\.venv\Scripts\python.exe -m emboss_lab
```

### Запуск в VS Code

1. Выберите **Файл → Открыть папку** и откройте `emboss-lab`. В проводнике VS Code должны быть видны `pyproject.toml`, `python` и `rust`.
2. Для запуска из встроенного PowerShell выберите **Терминал → Создать терминал** и выполните команду выше из корня проекта.
3. Для запуска с отладкой откройте **Run and Debug** клавишами `Ctrl+Shift+D`, выберите **EMBOSS LAB** и нажмите `F5`. Готовая конфигурация использует `.venv\Scripts\python.exe`. Для неё нужны расширения Microsoft **Python** и **Python Debugger**. [Документация VS Code](https://code.visualstudio.com/docs/python/debugging)

Если VS Code просит выбрать интерпретатор, откройте палитру команд клавишами `Ctrl+Shift+P`, выполните **Python: Select Interpreter** и выберите `.venv` с Python 3.12.

Если `.venv` ещё нет или вы изменили код Rust, выполните команды из раздела [«Сборка на Windows 11»](#сборка-на-windows-11) перед запуском.

Откройте свой несжатый 24-битный BMP или создайте пример по команде из раздела [«Проверки и замеры»](#проверки-и-замеры). Выберите 1, 2, 4 или 8 потоков и нажмите «Начать обработку». Кнопка «Сохранить BMP» появится после сборки результата. «Журнал» открывает события рабочих потоков, а вкладка «Замеры» запускает отдельную серию на текущем исходнике.

Вход и выход используют BMP RGB24 (BI_RGB). Допустимы до 24 млн пикселей и до 16 384 пикселей на сторону. Оба предпросмотра автоматически вписываются в области с сохранением пропорций.

## Сборка на Windows 11

Нужны CPython 3.12 x64, Rust stable с целью `x86_64-pc-windows-msvc` и Visual Studio Build Tools с компонентами C++ и Windows SDK. PySide6 и Pillow устанавливаются через maturin по версиям из [pyproject.toml](pyproject.toml).

```powershell
py -3.12 -m venv .venv
$env:VIRTUAL_ENV = (Resolve-Path .venv).Path
$env:Path = "$env:VIRTUAL_ENV\Scripts;$env:Path"
$env:PYTHONUTF8 = '1'
python -m pip install maturin==1.9.6 pytest==8.4.2
maturin develop --release
python -m emboss_lab
```

`PYTHONUTF8` помогает maturin разобрать ответ pip при кириллице в пути проекта.

## Проверки и замеры

```powershell
cargo fmt --manifest-path rust/Cargo.toml --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml
python -m pytest -q
```

Эти команды создают образец для повторения эксперимента с разрешением 3840 × 2160.

```powershell
python .\tools\make_sample.py .\samples\Мост-4K.bmp --width 3840
python -X utf8 -m emboss_lab.benchmarks .\samples\Мост-4K.bmp
```

## Исходный код

| Файл | Ответственность |
|---|---|
| [window.py](python/emboss_lab/window.py) и [widgets.py](python/emboss_lab/widgets.py) | Окно, состояния запуска, предпросмотр и индикация полос |
| [image_io.py](python/emboss_lab/image_io.py) | Проверка заголовка, декодирование, запись BMP через временный файл |
| [workers.py](python/emboss_lab/workers.py) | Фоновые задачи Qt и создание предпросмотра |
| [emboss.rs](rust/src/emboss.rs) и [pool.rs](rust/src/pool.rs) | Тиснение, очередь, потоки, прогресс, отмена и сборка |
| [lib.rs](rust/src/lib.rs) | Интерфейс PyO3 и освобождение GIL |
| [benchmarks.py](python/emboss_lab/benchmarks.py) | Одна методика замеров для окна и командной строки |

Клавиши `Ctrl+O`, `Ctrl+Enter`, `Ctrl+S`, `Ctrl+L` открывают BMP, запускают расчёт, сохраняют результат и переключают журнал. `F1` открывает краткое описание метода.

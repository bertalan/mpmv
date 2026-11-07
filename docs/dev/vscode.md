# VS Code: tips and tricks

The repository ships a shared VS Code setup in `.vscode/`: settings, recommended extensions, debug
configurations and tasks. It is the MPMV counterpart of the Fabric "VS Code tips and tricks" page
(https://docs.fabricmc.net/develop/getting-started/vscode/tips-and-tricks), adapted to Python and
Django.

## First run

1. Run `make install` once. It creates `.venv`, installs the Playwright browser and the git hooks.
2. Open the folder in VS Code and accept the **recommended extensions**: Python, Pylance, debugpy,
   Ruff, Mypy, Playwright, EditorConfig, TOML and Mermaid preview.
3. Check the interpreter in the status bar. It must be `.venv/bin/python`; if it is not, run
   *Python: Select Interpreter*.

## Navigating the code and the framework

| Action | Shortcut (macOS) | Notes |
|---|---|---|
| Quick Open a file | ⌘P | Type part of the path: `tax/engine` |
| Go to a symbol in the workspace | ⌘T | Type a class or function name: `compute_commission`, `TaxDecision` |
| Go to a symbol in the file | ⇧⌘O | Type `:` to group symbols by kind |
| Go to Definition | F12 or ⌘-click | Also works into Django and Wagtail sources in `.venv` |
| Peek Definition | ⌥F12 | Shows the definition inline, without leaving the file |
| Go to Implementations | ⌘F12 | Finds the classes that implement a `Protocol` (e.g. `PaymentGateway`) |
| Find All References | ⇧F12 | Every use of a model, service or setting |
| Rename Symbol | F2 | Renames across the project and updates imports |

Reading framework code is the Python equivalent of browsing the game sources in Fabric. Use
F12 on `models.Model`, `QuerySet` or a Wagtail class to read its implementation in `.venv`.

### When navigation breaks

The usual cause is the wrong interpreter or a stale index. In this order:

1. *Python: Select Interpreter* → `.venv/bin/python`.
2. *Python: Clear Cache and Reload Window*.
3. *Pylance: Restart Language Server*.
4. If imports from `apps.*` are still red, run `uv sync`, then reload the window.

## Tests (Testing view, flask icon)

The three suites from [ADR-0006](../adr/0006-testing-strategy.md) appear as folders:
`tests/unit`, `tests/integration`, `tests/e2e`, and later `apps/<app>/tests/{unit,integration}`.

- Run or debug a single test from the gutter arrow next to `def test_…`.
- Integration and e2e tests need the database: run the task **db: start** first (⇧⌘P →
  *Tasks: Run Task*), or `make db` in a terminal.
- The e2e suite runs headless. To watch the browser, launch the debug configuration
  **Pytest: e2e headed**. The Playwright extension can also record a journey (*Record new*) and
  generate the first draft of a test.
- Screenshots are saved to `build/e2e/screenshots/`. Open them from the Explorer.

TDD loop: write the failing test, run it from the gutter (red), implement, run it again (green),
refactor. Run **test: unit + integration** (⇧⌘P → *Tasks: Run Test Task*) before pushing.

## Debugging

Debug configurations (Run and Debug view, ⇧⌘D):

| Configuration | Use it for |
|---|---|
| Django: runserver (dev) | Breakpoints in views, services and templates; starts the database first |
| Pytest: current file | Debug the tests in the open file, stopping at the first failure |
| Pytest: unit suite | Debug pure engines with no database |
| Pytest: e2e headed | Watch the browser step by step (slow motion 300 ms) |

`justMyCode` is off for the test configurations, so you can step into Django when a test fails
for reasons inside the framework.

## Tasks

⇧⌘P → *Tasks: Run Task* runs the main Makefile targets: database start and stop, the three test
suites, `make check`, and rebuilding or opening the HTML spec.

## Formatting and checks on save

Ruff formats the file and fixes imports on save. Mypy runs in the editor with the same strict
config as `make check`. The pre-commit hooks repeat these checks at commit time, together with the
backlog freshness check.

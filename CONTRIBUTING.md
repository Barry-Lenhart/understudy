# Contributing

Thanks for your interest in understudy! Issues and pull requests are welcome.

## Getting started

```sh
git clone https://github.com/Barry-Lenhart/understudy.git && cd understudy
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The test suite uses a fake builder (`tests/fake_builder.py`), so it runs without Ollama or OpenCode.

## Before opening a pull request

- Add or update tests for behaviour changes.
- Run `ruff check .` and `ruff format .`.
- Add a line to `CHANGELOG.md`.

## Reporting bugs

Please include the model and builder you used, the task you delegated, and the `status` and `builder_output`
from the result.

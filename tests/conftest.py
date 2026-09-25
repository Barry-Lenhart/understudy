import json
import subprocess
import sys
from pathlib import Path

import pytest

from understudy.builders import CommandBuilder
from understudy.config import Config
from understudy.runner import Runner

FAKE_BUILDER = Path(__file__).with_name("fake_builder.py")

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
STILL_BUGGY = "def add(a, b):\n    return a * b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
TEST_COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Keep test worktrees out of the real ~/.cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Test User")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "test@example.com")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small project whose `add` function is buggy."""
    path = tmp_path / "project"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "calc.py").write_text(BUGGY)
    (path / "test_calc.py").write_text(TESTS)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "initial")
    return path


@pytest.fixture
def fake(tmp_path: Path, monkeypatch):
    """Configure the fake builder. Call it with a list of {path: content} dicts,
    one per builder call; returns a function that reads the recorded prompts."""
    state = tmp_path / "fake-builder"

    def configure(*steps: dict) -> None:
        monkeypatch.setenv("FAKE_BUILDER_STATE", str(state))
        monkeypatch.setenv("FAKE_BUILDER_SCRIPT", json.dumps([{"files": s} for s in steps]))

    def prompts() -> list[str]:
        log = state / "prompts.log"
        if not log.exists():
            return []
        return [p for p in log.read_text().split("=== call ") if p.strip()]

    configure.prompts = prompts
    return configure


@pytest.fixture
def runner() -> Runner:
    config = Config(builder="command", builder_command="unused", max_rounds=3)
    return Runner(
        config, builder=CommandBuilder(f"{sys.executable} {FAKE_BUILDER} {{prompt_file}}")
    )

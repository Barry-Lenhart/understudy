from pathlib import Path

import pytest

from understudy.builders import CommandBuilder, OpenCodeBuilder, make_builder
from understudy.config import Config
from understudy.errors import UnderstudyError
from understudy.runner import build_prompt


def test_defaults():
    config = Config.from_env({})
    assert config == Config()
    assert isinstance(make_builder(config), OpenCodeBuilder)


def test_from_env():
    config = Config.from_env(
        {
            "UNDERSTUDY_BUILDER": "command",
            "UNDERSTUDY_BUILDER_COMMAND": "aider --message-file {prompt_file}",
            "UNDERSTUDY_MODEL": "ollama/devstral",
            "UNDERSTUDY_MAX_ROUNDS": "5",
            "UNDERSTUDY_TEST_TIMEOUT": "30",
        }
    )
    assert config.builder == "command"
    assert config.model == "ollama/devstral"
    assert config.max_rounds == 5
    assert config.test_timeout == 30
    assert isinstance(make_builder(config), CommandBuilder)


@pytest.mark.parametrize(
    "env, message",
    [
        ({"UNDERSTUDY_BUILDER": "gpt"}, "UNDERSTUDY_BUILDER must be one of"),
        ({"UNDERSTUDY_BUILDER": "command"}, "requires UNDERSTUDY_BUILDER_COMMAND"),
        ({"UNDERSTUDY_MAX_ROUNDS": "three"}, "must be a whole number"),
        ({"UNDERSTUDY_MAX_ROUNDS": "0"}, "must be at least 1"),
    ],
)
def test_invalid_env(env, message):
    with pytest.raises(UnderstudyError, match=message):
        Config.from_env(env)


def test_prompt_first_round():
    prompt = build_prompt("Add a sub() function.", "pytest -q", "", [], Path("/work/tree"))
    assert "The project is in the directory /work/tree ." in prompt
    assert "/work/tree/README.md" in prompt
    assert "TASK:\nAdd a sub() function." in prompt
    assert "`pytest -q`" in prompt
    assert "FAILED" not in prompt
    assert "FEEDBACK" not in prompt


def test_prompt_with_failure_and_feedback():
    prompt = build_prompt(
        "Add sub().", "", "assert 1 == 2", ["Handle negatives.", "Add a docstring."], Path("/w")
    )
    assert "must pass" not in prompt  # no test command
    assert "FAILED the tests" in prompt
    assert "assert 1 == 2" in prompt
    assert "- Handle negatives.\n- Add a docstring." in prompt


def test_diff_warnings():
    from understudy.runner import diff_warnings

    diff = "-def old():\n-def test_old():\n+def new():\n-def moved():\n+def moved(x):\n"
    assert diff_warnings(diff) == [
        "existing tests were deleted: test_old. Passing tests may only mean that "
        "failing ones were removed.",
        "existing functions were deleted: old",
    ]
    assert diff_warnings("+def added():\n") == []


def test_worktree_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert Config().worktree_root() == tmp_path / "understudy" / "worktrees"
    config = Config.from_env({"UNDERSTUDY_WORKTREE_DIR": str(tmp_path / "wt")})
    assert config.worktree_root() == tmp_path / "wt"

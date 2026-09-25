"""Settings, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import UnderstudyError

BUILDERS = ("opencode", "command")


@dataclass(frozen=True)
class Config:
    #: Which builder runs the local model: "opencode" or "command".
    builder: str = "opencode"
    #: Model passed to OpenCode, e.g. "ollama/qwen3:8b".
    model: str = "ollama/qwen3:8b"
    #: For builder="command": the command to run. "{prompt_file}" and
    #: "{workdir}" are replaced with the prompt file and the worktree path.
    builder_command: str | None = None
    #: How many build -> test rounds to try before giving up.
    max_rounds: int = 3
    #: Seconds allowed for one builder run.
    builder_timeout: int = 900
    #: Seconds allowed for one test run.
    test_timeout: int = 600
    #: Diffs longer than this are truncated in tool results.
    max_diff_chars: int = 20000
    #: Test output returned to Claude is cut to this many trailing characters.
    max_output_chars: int = 4000
    #: Where worktrees are created. Defaults to $XDG_CACHE_HOME/understudy/worktrees
    #: (~/.cache/understudy/worktrees). Must be outside the repository's .git
    #: directory: tools like OpenCode refuse to edit files there.
    worktree_dir: Path | None = None

    def worktree_root(self) -> Path:
        if self.worktree_dir is not None:
            return self.worktree_dir
        cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        return Path(cache) / "understudy" / "worktrees"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        defaults = cls()

        def integer(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            try:
                value = int(raw)
            except ValueError:
                raise UnderstudyError(f"{name} must be a whole number, got {raw!r}") from None
            if value < 1:
                raise UnderstudyError(f"{name} must be at least 1, got {value}")
            return value

        builder = env.get("UNDERSTUDY_BUILDER") or defaults.builder
        if builder not in BUILDERS:
            raise UnderstudyError(
                f"UNDERSTUDY_BUILDER must be one of {', '.join(BUILDERS)}, got {builder!r}"
            )
        builder_command = env.get("UNDERSTUDY_BUILDER_COMMAND") or None
        if builder == "command" and not builder_command:
            raise UnderstudyError("UNDERSTUDY_BUILDER=command requires UNDERSTUDY_BUILDER_COMMAND")

        return cls(
            builder=builder,
            model=env.get("UNDERSTUDY_MODEL") or defaults.model,
            builder_command=builder_command,
            max_rounds=integer("UNDERSTUDY_MAX_ROUNDS", defaults.max_rounds),
            builder_timeout=integer("UNDERSTUDY_BUILDER_TIMEOUT", defaults.builder_timeout),
            test_timeout=integer("UNDERSTUDY_TEST_TIMEOUT", defaults.test_timeout),
            max_diff_chars=integer("UNDERSTUDY_MAX_DIFF_CHARS", defaults.max_diff_chars),
            max_output_chars=integer("UNDERSTUDY_MAX_OUTPUT_CHARS", defaults.max_output_chars),
            worktree_dir=(
                Path(env["UNDERSTUDY_WORKTREE_DIR"]).expanduser()
                if env.get("UNDERSTUDY_WORKTREE_DIR")
                else None
            ),
        )

"""Builders run a local model that edits files in a worktree."""

from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Config
from .gitops import run


@dataclass
class BuildResult:
    ok: bool
    output: str


class Builder(Protocol):
    def build(self, prompt: str, workdir: Path, timeout: int) -> BuildResult: ...


class OpenCodeBuilder:
    """Runs `opencode run` with a local model (e.g. an Ollama model).

    OpenCode must already be configured with the model's provider."""

    def __init__(self, model: str) -> None:
        self.model = model

    def build(self, prompt: str, workdir: Path, timeout: int) -> BuildResult:
        code, out = run(
            ["opencode", "run", prompt, "-m", self.model, "--dir", str(workdir)],
            cwd=workdir,
            timeout=timeout,
        )
        return BuildResult(ok=code == 0, output=out)


class CommandBuilder:
    """Runs any command. "{prompt_file}" and "{workdir}" in the command are
    replaced with a file containing the prompt and the worktree path.

    Example (aider): aider --model ollama_chat/qwen3:8b --yes --message-file {prompt_file}
    """

    def __init__(self, command: str) -> None:
        self.args = shlex.split(command)

    def build(self, prompt: str, workdir: Path, timeout: int) -> BuildResult:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(prompt)
            prompt_file = f.name
        try:
            args = [
                a.replace("{prompt_file}", prompt_file).replace("{workdir}", str(workdir))
                for a in self.args
            ]
            code, out = run(args, cwd=workdir, timeout=timeout)
        finally:
            Path(prompt_file).unlink(missing_ok=True)
        return BuildResult(ok=code == 0, output=out)


def make_builder(config: Config) -> Builder:
    if config.builder == "command":
        assert config.builder_command
        return CommandBuilder(config.builder_command)
    return OpenCodeBuilder(config.model)

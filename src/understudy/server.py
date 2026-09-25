"""MCP server exposing understudy's tools to Claude Code (or any MCP client)."""

from __future__ import annotations

import os
from pathlib import Path

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .config import Config
from .errors import UnderstudyError
from .runner import Runner

INSTRUCTIONS = """\
understudy hands routine, well-specified coding tasks to a local model so they
don't use your own context or quota.

Use delegate() for small, self-contained changes that tests can check: adding
a function, fixing a clear bug, writing tests for existing code, mechanical
refactors. Keep tasks specific (name the files and the expected behaviour) and
always pass a test_command when the project has tests: the tests are the gate.
Keep hard, subtle or architectural work for yourself.

delegate() works in an isolated git worktree and never touches the user's
files. When it returns, review the diff and test output yourself, then call
accept(id) to merge, revise(id, feedback) for another attempt, or discard(id).
"""

mcp = MCPServer(
    name="understudy",
    version=__version__,
    instructions=INSTRUCTIONS,
    website_url="https://github.com/Barry-Lenhart/understudy",
)


def _repo(repo_path: str) -> Path:
    return Path(repo_path).expanduser() if repo_path else Path.cwd()


def _runner() -> Runner:
    try:
        return Runner(Config.from_env())
    except UnderstudyError as e:
        raise ToolError(str(e)) from e


async def _in_thread(ctx: Context | None, work):
    """Run blocking work in a thread, forwarding progress messages to the client."""
    step = 0

    def progress(message: str) -> None:
        nonlocal step
        step += 1
        if ctx is not None:
            try:
                anyio.from_thread.run(ctx.report_progress, step, None, message)
            except Exception:
                pass  # progress is best-effort

    try:
        return await anyio.to_thread.run_sync(lambda: work(progress))
    except UnderstudyError as e:
        raise ToolError(str(e)) from e


@mcp.tool(
    annotations=ToolAnnotations(title="Delegate a task to the local model", destructiveHint=False)
)
async def delegate(
    task: str, test_command: str = "", repo_path: str = "", ctx: Context | None = None
) -> dict:
    """Hand a coding task to the local model.

    The model edits an isolated git worktree; `test_command` (a shell command,
    e.g. "pytest -q") is run after each attempt and failures are fed back to the
    model, up to UNDERSTUDY_MAX_ROUNDS times. "{repo}" in the test command is
    replaced with the repository path, e.g. "{repo}/.venv/bin/pytest -q".

    Returns the diff and test output for you to review, plus an id for
    accept/revise/discard. `repo_path` defaults to the current directory.
    """
    runner = _runner()
    return await _in_thread(
        ctx, lambda progress: runner.delegate(_repo(repo_path), task, test_command, progress)
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Ask the local model to revise its work", destructiveHint=False
    )
)
async def revise(id: str, feedback: str, repo_path: str = "", ctx: Context | None = None) -> dict:
    """Send review feedback on a delegation; the local model tries again and
    the tests are re-run. Returns the updated diff and test output."""
    runner = _runner()
    return await _in_thread(
        ctx, lambda progress: runner.revise(_repo(repo_path), id, feedback, progress)
    )


@mcp.tool(annotations=ToolAnnotations(title="Merge a delegation", destructiveHint=False))
async def accept(id: str, commit_message: str = "", repo_path: str = "") -> dict:
    """Merge a delegation into the current branch as a single commit (the task
    text is used as the message unless `commit_message` is given), then clean
    up its worktree. Refuses if the working tree has uncommitted changes."""
    runner = _runner()
    return await _in_thread(None, lambda _: runner.accept(_repo(repo_path), id, commit_message))


@mcp.tool(annotations=ToolAnnotations(title="Discard a delegation", destructiveHint=True))
async def discard(id: str, repo_path: str = "") -> dict:
    """Throw away a delegation's worktree and branch without merging."""
    runner = _runner()
    return await _in_thread(None, lambda _: runner.discard(_repo(repo_path), id))


@mcp.tool(annotations=ToolAnnotations(title="List delegations", readOnlyHint=True))
async def status(repo_path: str = "") -> list[dict]:
    """List delegations in this repository that have not been accepted or discarded."""
    runner = _runner()
    return await _in_thread(None, lambda _: runner.status(_repo(repo_path)))


def main() -> None:
    if os.environ.get("UNDERSTUDY_CHDIR"):
        os.chdir(os.environ["UNDERSTUDY_CHDIR"])
    mcp.run("stdio")


if __name__ == "__main__":
    main()

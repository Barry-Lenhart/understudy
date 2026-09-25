"""The build -> test loop, and the accept/revise/discard operations.

Everything here is synchronous and independent of MCP so it can be tested
directly; server.py wraps it as MCP tools."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import gitops
from .builders import Builder, make_builder
from .config import Config
from .errors import UnderstudyError
from .state import Delegation, Round

#: Called with (message) as work progresses, e.g. to send MCP progress updates.
Progress = Callable[[str], None]

NEXT_STEPS = (
    "Review the diff and test output. Then call accept(id) to merge it, "
    "revise(id, feedback) to have the local model try again with your feedback, "
    "or discard(id) to throw it away."
)


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _tail(text: str, limit: int) -> str:
    text = _ANSI_ESCAPE.sub("", text).strip()
    if len(text) <= limit:
        return text
    return "[... earlier output cut ...]\n" + text[-limit:]


def build_prompt(
    task: str, test_command: str, last_failure: str, feedback: list[str], workdir: Path
) -> str:
    parts = [
        "You are working in a git checkout of a software project. Make the change "
        "described below by editing its files.",
        "",
        # Small local models often invent paths; spelling out the directory with
        # an example absolute path keeps their edits inside the worktree.
        f"The project is in the directory {workdir} . Every file you read or write must "
        f"be inside it; use absolute paths such as {workdir / 'README.md'} .",
        "",
        "Rules:",
        "- Read each file before you change it.",
        "- Keep all existing code, tests and comments unless the task says to remove them.",
        "- Make targeted edits; do not rewrite whole files.",
        "- Change only what the task needs. Do not reformat or rewrite unrelated code.",
        "- Follow the existing style of the surrounding code.",
        "- Do not run git commands; your changes are committed for you.",
        "",
        "TASK:",
        task.strip(),
    ]
    if test_command:
        parts += ["", f"The project's tests are run with `{test_command}` and must pass."]
    if feedback:
        parts += ["", "REVIEWER FEEDBACK on the current version (address all of it):"]
        parts += [f"- {item.strip()}" for item in feedback]
    if last_failure:
        parts += [
            "",
            "Your previous attempt FAILED the tests. Fix the code so they pass. Test output:",
            last_failure,
        ]
    return "\n".join(parts) + "\n"


def run_tests(test_command: str, worktree: Path, repo: Path, timeout: int) -> tuple[bool, str]:
    """Run the test command (a shell command) in the worktree.

    "{repo}" in the command is replaced with the main repository path, so a
    project's virtualenv can be used, e.g. "{repo}/.venv/bin/pytest -q"."""
    command = test_command.replace("{repo}", str(repo))
    # No bytecode caches: a stale .pyc can hide a same-size edit made within
    # the same second (Python only checks mtime and size).
    env = {
        **os.environ,
        "PWD": str(worktree),
        "UNDERSTUDY_REPO": str(repo),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=worktree,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"tests timed out after {timeout}s"
    return proc.returncode == 0, proc.stdout + proc.stderr


_REMOVED_DEF = re.compile(r"^-\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
_ADDED_DEF = re.compile(r"^\+\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)


def diff_warnings(diff_text: str) -> list[str]:
    """Flag changes a reviewer should look at closely: existing functions or
    tests that were deleted (and not re-added elsewhere in the diff)."""
    removed = set(_REMOVED_DEF.findall(diff_text)) - set(_ADDED_DEF.findall(diff_text))
    tests = sorted(name for name in removed if name.startswith("test"))
    functions = sorted(name for name in removed if not name.startswith("test"))
    warnings = []
    if tests:
        warnings.append(
            f"existing tests were deleted: {', '.join(tests)}. Passing tests may only "
            "mean that failing ones were removed."
        )
    if functions:
        warnings.append(f"existing functions were deleted: {', '.join(functions)}")
    return warnings


class Runner:
    def __init__(self, config: Config, builder: Builder | None = None) -> None:
        self.config = config
        self.builder = builder or make_builder(config)

    # -- public operations ------------------------------------------------- #

    def delegate(
        self, repo_path: Path, task: str, test_command: str = "", progress: Progress | None = None
    ) -> dict:
        if not task.strip():
            raise UnderstudyError("task must not be empty")
        repo = gitops.repo_root(repo_path)
        state_dir = gitops.state_dir(repo)
        delegation_id = Delegation.new_id()
        worktree = self.config.worktree_root() / f"{repo.name}-{delegation_id}"
        branch = f"understudy/{delegation_id}"
        base = gitops.head_commit(repo)
        gitops.add_worktree(repo, worktree, branch, base)
        d = Delegation(
            id=delegation_id,
            task=task,
            test_command=test_command.strip(),
            repo=str(repo),
            worktree=str(worktree),
            branch=branch,
            base=base,
        )
        d.save(state_dir)
        self._build_until_green(d, state_dir, progress)
        return self._result(d)

    def revise(
        self, repo_path: Path, delegation_id: str, feedback: str, progress: Progress | None = None
    ) -> dict:
        if not feedback.strip():
            raise UnderstudyError("feedback must not be empty")
        repo = gitops.repo_root(repo_path)
        state_dir = gitops.state_dir(repo)
        d = Delegation.load(state_dir, delegation_id)
        d.feedback.append(feedback.strip())
        self._build_until_green(d, state_dir, progress)
        return self._result(d)

    def accept(self, repo_path: Path, delegation_id: str, commit_message: str = "") -> dict:
        repo = gitops.repo_root(repo_path)
        state_dir = gitops.state_dir(repo)
        d = Delegation.load(state_dir, delegation_id)
        if gitops.has_tracked_changes(repo):
            raise UnderstudyError(
                "the main working tree has uncommitted changes; "
                "commit or stash them, then accept again"
            )
        message = commit_message.strip() or d.task.strip().splitlines()[0][:72]
        commit = gitops.squash_merge(repo, d.branch, message)
        gitops.remove_worktree(repo, Path(d.worktree), d.branch)
        d.delete(state_dir)
        return {
            "id": d.id,
            "status": "merged",
            "commit": commit,
            "branch": gitops.current_branch(repo),
            "message": message,
        }

    def discard(self, repo_path: Path, delegation_id: str) -> dict:
        repo = gitops.repo_root(repo_path)
        state_dir = gitops.state_dir(repo)
        d = Delegation.load(state_dir, delegation_id)
        gitops.remove_worktree(repo, Path(d.worktree), d.branch)
        d.delete(state_dir)
        return {"id": d.id, "status": "discarded"}

    def status(self, repo_path: Path) -> list[dict]:
        repo = gitops.repo_root(repo_path)
        return [
            {
                "id": d.id,
                "status": d.status,
                "task": d.task,
                "rounds": len(d.rounds),
                "created_at": d.created_at,
            }
            for d in Delegation.load_all(gitops.state_dir(repo))
        ]

    # -- internals ---------------------------------------------------------- #

    def _build_until_green(self, d: Delegation, state_dir: Path, progress: Progress | None) -> None:
        worktree, repo = Path(d.worktree), Path(d.repo)
        report = progress or (lambda message: None)
        last_failure = ""
        previous_status = d.status
        start_commit = gitops.head_commit(worktree)
        d.status = "building"
        first_new_round = len(d.rounds)
        for attempt in range(1, self.config.max_rounds + 1):
            number = len(d.rounds) + 1
            report(f"round {attempt}/{self.config.max_rounds}: local model is working")
            prompt = build_prompt(d.task, d.test_command, last_failure, d.feedback, worktree)
            result = self.builder.build(prompt, worktree, self.config.builder_timeout)
            changed = gitops.commit_all(worktree, f"understudy round {number}")
            round_ = Round(
                number=number,
                builder_ok=result.ok,
                changed=changed,
                tests_passed=None,
                builder_output=_tail(result.output, self.config.max_output_chars),
            )
            d.rounds.append(round_)

            if not d.test_command:
                d.status = "built_untested"
                break
            report(f"round {attempt}/{self.config.max_rounds}: running tests")
            passed, output = run_tests(d.test_command, worktree, repo, self.config.test_timeout)
            # The model's changes are already committed, so anything the test
            # run left behind is a side effect and must not be committed later.
            gitops.reset_worktree(worktree)
            round_.tests_passed = passed
            round_.test_output = _tail(output, self.config.max_output_chars)
            if passed:
                d.status = "tests_passed"
                break
            last_failure = round_.test_output
            d.status = "tests_failing"
        latest = d.rounds[-1]
        if d.status == "tests_failing" and previous_status == "tests_passed":
            # Never lose a version that passed: undo this call's attempts.
            gitops.reset_to(worktree, start_commit)
            d.status = "revision_failed"
        elif not latest.builder_ok and not latest.changed:
            d.status = "builder_failed"
        elif not gitops.diff(worktree, d.base).strip():
            d.status = "no_changes"
        elif d.feedback and not any(r.changed for r in d.rounds[first_new_round:]):
            d.status = "revision_made_no_changes"
        d.save(state_dir)

    def _result(self, d: Delegation) -> dict:
        worktree = Path(d.worktree)
        full_diff = gitops.diff(worktree, d.base)
        truncated = len(full_diff) > self.config.max_diff_chars
        last = d.rounds[-1] if d.rounds else None
        result = {
            "id": d.id,
            "status": d.status,
            "rounds_used": len(d.rounds),
            "branch": d.branch,
            "worktree": d.worktree,
            "diff_stat": gitops.diff_stat(worktree, d.base),
            "diff": full_diff[: self.config.max_diff_chars],
            "diff_truncated": truncated,
            "test_output": last.test_output if last else "",
        }
        warnings = diff_warnings(full_diff)
        if warnings:
            result["warnings"] = warnings
        if d.status == "revision_failed":
            result["next_steps"] = (
                "The revision broke the tests every time, so it was rolled back: the diff "
                "below is the previous version, which passed. test_output shows the last "
                "failed attempt. Call accept(id) to take the previous version, "
                "revise(id, feedback) to try again, or discard(id)."
            )
        elif d.status == "builder_failed":
            result["builder_output"] = last.builder_output if last else ""
            result["next_steps"] = (
                "The local model's tool failed before making changes; see builder_output. "
                "Check that the model is available and configured (UNDERSTUDY_MODEL), then call "
                "revise(id, feedback) to retry, or discard(id)."
            )
        elif d.status == "revision_made_no_changes":
            result["builder_output"] = last.builder_output if last else ""
            result["next_steps"] = (
                "The local model did not change anything in response to the feedback, so the "
                "diff is the same as before. Call revise(id, feedback) with more specific "
                "instructions, discard(id), or make the change yourself."
            )
        elif d.status == "no_changes":
            result["builder_output"] = last.builder_output if last else ""
            result["next_steps"] = (
                "The local model made no changes. Call discard(id), or revise(id, feedback) "
                "with more specific instructions."
            )
        else:
            result["next_steps"] = NEXT_STEPS
        if truncated:
            result["next_steps"] += f" The diff was truncated; the full change is in {d.worktree}."
        return result

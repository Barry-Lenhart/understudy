"""Thin wrappers around the git commands understudy needs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import UnderstudyError

# Commits made inside the worktree by understudy itself. They are squashed away
# on accept, so they never end up in the user's history under this identity.
_WORKTREE_IDENTITY = ["-c", "user.name=understudy", "-c", "user.email=understudy@localhost"]


def run(args: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    """Run a command, returning (exit code, combined output)."""
    # Keep PWD in sync with cwd: some tools (e.g. OpenCode) trust $PWD over the
    # real working directory, and would otherwise work in the wrong folder.
    env = {**os.environ, "PWD": str(cwd)}
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s: {' '.join(args)}"
    except FileNotFoundError:
        return 127, f"command not found: {args[0]}"
    return proc.returncode, proc.stdout + proc.stderr


def git(repo: Path, *args: str, timeout: int = 120) -> str:
    code, out = run(["git", *args], cwd=repo, timeout=timeout)
    if code != 0:
        raise UnderstudyError(f"git {' '.join(args)} failed:\n{out.strip()}")
    return out


def repo_root(path: Path) -> Path:
    code, out = run(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if code != 0:
        raise UnderstudyError(f"{path} is not inside a git repository")
    root = Path(out.strip())
    if run(["git", "rev-parse", "--verify", "HEAD"], cwd=root)[0] != 0:
        raise UnderstudyError(f"{root} has no commits yet; make an initial commit first")
    return root


def state_dir(repo: Path) -> Path:
    """Directory inside .git where understudy keeps its state files.

    Being inside .git keeps it out of the user's working tree and out of
    `git status`."""
    common = Path(git(repo, "rev-parse", "--git-common-dir").strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common / "understudy"


def head_commit(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def add_worktree(repo: Path, path: Path, branch: str, base: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", branch, str(path), base)


def commit_all(worktree: Path, message: str) -> bool:
    """Commit every change in the worktree. Returns False if nothing changed."""
    git(worktree, "add", "-A")
    if run(["git", "diff", "--cached", "--quiet"], cwd=worktree)[0] == 0:
        return False
    git(worktree, *_WORKTREE_IDENTITY, "commit", "-q", "--no-verify", "-m", message)
    return True


def reset_worktree(worktree: Path) -> None:
    """Drop uncommitted changes and untracked files (ignored files are kept)."""
    git(worktree, "checkout", "-q", "--", ".")
    git(worktree, "clean", "-fdq")


def reset_to(worktree: Path, commit: str) -> None:
    """Move the worktree's branch back to `commit`, discarding later commits."""
    git(worktree, "reset", "-q", "--hard", commit)
    git(worktree, "clean", "-fdq")


def diff(worktree: Path, base: str) -> str:
    return git(worktree, "diff", base, "HEAD")


def diff_stat(worktree: Path, base: str) -> str:
    return git(worktree, "diff", "--stat", base, "HEAD").strip()


def has_tracked_changes(repo: Path) -> bool:
    return bool(git(repo, "status", "--porcelain", "--untracked-files=no").strip())


def squash_merge(repo: Path, branch: str, message: str) -> str:
    """Squash-merge `branch` into the current branch as a single commit.

    Returns the new commit hash. On a conflict the merge is rolled back."""
    code, out = run(["git", "merge", "--squash", branch], cwd=repo)
    if code != 0:
        run(["git", "reset", "--merge"], cwd=repo)
        raise UnderstudyError(f"could not merge {branch} (conflict?):\n{out.strip()}")
    git(repo, "commit", "-q", "-m", message)
    return head_commit(repo)


def remove_worktree(repo: Path, path: Path, branch: str) -> None:
    run(["git", "worktree", "remove", "--force", str(path)], cwd=repo)
    run(["git", "worktree", "prune"], cwd=repo)
    run(["git", "branch", "-D", branch], cwd=repo)

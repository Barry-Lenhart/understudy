import sys

import pytest
from conftest import BUGGY, FAKE_BUILDER, FIXED, STILL_BUGGY, TEST_COMMAND, git

from understudy import gitops
from understudy.builders import CommandBuilder
from understudy.config import Config
from understudy.errors import UnderstudyError
from understudy.runner import Runner
from understudy.state import Delegation

TASK = "Fix add() in calc.py so it adds its arguments."


def test_passes_first_round(repo, runner, fake):
    fake({"calc.py": FIXED})

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["status"] == "tests_passed"
    assert result["rounds_used"] == 1
    assert "+    return a + b" in result["diff"]
    assert "calc.py" in result["diff_stat"]
    assert "1 passed" in result["test_output"]
    # The user's files are untouched until accept().
    assert (repo / "calc.py").read_text() == BUGGY


def test_test_failures_are_fed_back(repo, runner, fake):
    fake({"calc.py": STILL_BUGGY}, {"calc.py": FIXED})

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["status"] == "tests_passed"
    assert result["rounds_used"] == 2
    first, second = fake.prompts()
    assert "FAILED the tests" not in first
    assert "FAILED the tests" in second
    assert "assert 6 == 5" in second  # the pytest failure from round 1


def test_gives_up_after_max_rounds(repo, runner, fake):
    fake({"calc.py": STILL_BUGGY})

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["status"] == "tests_failing"
    assert result["rounds_used"] == 3
    assert "assert 6 == 5" in result["test_output"]


def test_no_changes(repo, runner, fake):
    fake()

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["status"] == "no_changes"
    assert result["diff"] == ""
    assert "made no changes" in result["next_steps"]


def test_without_test_command(repo, runner, fake):
    fake({"calc.py": FIXED})

    result = runner.delegate(repo, TASK)

    assert result["status"] == "built_untested"
    assert result["rounds_used"] == 1


def test_repo_placeholder_in_test_command(repo, runner, fake):
    fake({"calc.py": FIXED})

    result = runner.delegate(
        repo, TASK, 'test -f "{repo}/calc.py" && test "$UNDERSTUDY_REPO" = "{repo}"'
    )

    assert result["status"] == "tests_passed"


def test_accept_merges_one_commit_and_cleans_up(repo, runner, fake):
    fake({"calc.py": FIXED})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]

    result = runner.accept(repo, delegation_id, "Fix add()")

    assert result["status"] == "merged"
    assert (repo / "calc.py").read_text() == FIXED
    assert git(repo, "log", "--format=%s").splitlines() == ["Fix add()", "initial"]
    assert git(repo, "log", "-1", "--format=%an").strip() == "Test User"
    assert "understudy/" not in git(repo, "branch")
    assert runner.status(repo) == []


def test_accept_uses_task_as_default_message(repo, runner, fake):
    fake({"calc.py": FIXED})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]

    runner.accept(repo, delegation_id)

    assert git(repo, "log", "-1", "--format=%s").strip() == TASK


def test_accept_refuses_with_uncommitted_changes(repo, runner, fake):
    fake({"calc.py": FIXED})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]
    (repo / "test_calc.py").write_text("# local edit\n")

    with pytest.raises(UnderstudyError, match="uncommitted changes"):
        runner.accept(repo, delegation_id)
    assert (repo / "calc.py").read_text() == BUGGY


def test_accept_rolls_back_on_conflict(repo, runner, fake):
    fake({"calc.py": FIXED})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    git(repo, "commit", "-qam", "user change")

    with pytest.raises(UnderstudyError, match="could not merge"):
        runner.accept(repo, delegation_id)
    assert not gitops.has_tracked_changes(repo)
    assert [d["id"] for d in runner.status(repo)] == [delegation_id]


def test_discard(repo, runner, fake):
    fake({"calc.py": FIXED})
    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert runner.discard(repo, result["id"]) == {"id": result["id"], "status": "discarded"}
    assert runner.status(repo) == []
    assert "understudy/" not in git(repo, "branch")
    assert (repo / "calc.py").read_text() == BUGGY


def test_revise_sends_feedback_and_retests(repo, runner, fake):
    fake({"calc.py": FIXED}, {"calc.py": FIXED + "\n\ndef sub(a, b):\n    return a - b\n"})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]

    result = runner.revise(repo, delegation_id, "Also add a sub() function.")

    assert result["status"] == "tests_passed"
    assert result["rounds_used"] == 2
    assert "def sub(a, b):" in result["diff"]
    assert "Also add a sub() function." in fake.prompts()[-1]


def test_status_lists_open_delegations(repo, runner, fake):
    fake({"calc.py": FIXED})
    result = runner.delegate(repo, TASK, TEST_COMMAND)

    [entry] = runner.status(repo)
    assert entry["id"] == result["id"]
    assert entry["status"] == "tests_passed"
    assert entry["task"] == TASK


def test_state_is_kept_out_of_the_working_tree(repo, runner, fake):
    fake({"calc.py": FIXED})
    runner.delegate(repo, TASK, TEST_COMMAND)

    assert git(repo, "status", "--porcelain") == ""


def test_diff_is_truncated(repo, fake):

    fake({"calc.py": FIXED + "# padding\n" * 200})
    runner = Runner(
        Config(builder="command", builder_command="unused", max_diff_chars=100),
        builder=CommandBuilder(f"{sys.executable} {FAKE_BUILDER} {{prompt_file}}"),
    )

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["diff_truncated"] is True
    assert len(result["diff"]) == 100
    assert "truncated" in result["next_steps"]


@pytest.mark.parametrize("task", ["", "   "])
def test_empty_task_is_rejected(repo, runner, task):
    with pytest.raises(UnderstudyError, match="task must not be empty"):
        runner.delegate(repo, task)


def test_not_a_git_repo(tmp_path, runner):
    with pytest.raises(UnderstudyError, match="not inside a git repository"):
        runner.delegate(tmp_path, TASK)


def test_repo_without_commits(tmp_path, runner):
    git(tmp_path, "init", "-q")
    with pytest.raises(UnderstudyError, match="no commits yet"):
        runner.delegate(tmp_path, TASK)


def test_unknown_id(repo, runner):
    with pytest.raises(UnderstudyError, match="no delegation with id"):
        runner.accept(repo, "abc123")
    with pytest.raises(UnderstudyError, match="no delegation with id"):
        runner.discard(repo, "../../etc")


def test_state_round_trip(repo, runner, fake):
    fake({"calc.py": FIXED})
    result = runner.delegate(repo, TASK, TEST_COMMAND)

    loaded = Delegation.load(gitops.state_dir(repo), result["id"])
    assert loaded.rounds[0].tests_passed is True
    assert loaded.test_command == TEST_COMMAND


def test_test_side_effects_are_not_committed(repo, runner, fake):
    fake({"calc.py": "def add(a, b):\n    return a * b\n"}, {"calc.py": FIXED})

    result = runner.delegate(repo, TASK, TEST_COMMAND + " && touch left-by-tests.txt")

    assert result["status"] == "tests_passed"
    assert "left-by-tests.txt" not in result["diff"]
    assert "calc.py" in result["diff_stat"]


def test_builder_runs_with_pwd_set_to_the_worktree(repo, fake, monkeypatch):
    monkeypatch.setenv("PWD", "/somewhere/else")
    fake({"calc.py": FIXED})
    runner = Runner(
        Config(builder="command", builder_command="unused"),
        builder=CommandBuilder('sh -c \'echo "pwd=$PWD"; echo "cwd=$(pwd -P)"\''),
    )

    result = runner.delegate(repo, TASK)

    output = result["builder_output"]
    pwd = output.split("pwd=")[1].split()[0]
    assert pwd == output.split("cwd=")[1].split()[0]
    assert pwd.endswith(result["id"])


def test_ansi_codes_are_removed_from_output(repo, fake):
    runner = Runner(
        Config(builder="command", builder_command="unused"),
        builder=CommandBuilder("printf '\\033[91mError:\\033[0m nothing to do'"),
    )

    result = runner.delegate(repo, TASK)

    assert result["builder_output"] == "Error: nothing to do"


def test_deleted_tests_and_functions_are_flagged(repo, runner, fake):
    # The "model" replaces the test file, dropping test_add, and removes add().
    fake(
        {
            "calc.py": "def mul(a, b):\n    return a * b\n",
            "test_calc.py": "def test_x():\n    pass\n",
        }
    )

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["status"] == "tests_passed"
    [tests_warning, functions_warning] = result["warnings"]
    assert "existing tests were deleted: test_add" in tests_warning
    assert functions_warning == "existing functions were deleted: add"


def test_no_warnings_for_modified_functions(repo, runner, fake):
    fake({"calc.py": FIXED})

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert "warnings" not in result


def test_revise_that_changes_nothing_is_reported(repo, runner, fake):
    fake({"calc.py": FIXED})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]

    result = runner.revise(repo, delegation_id, "Add a docstring.")

    assert result["status"] == "revision_made_no_changes"
    assert "did not change anything" in result["next_steps"]
    assert "+    return a + b" in result["diff"]


def test_builder_failure_is_reported(repo):
    runner = Runner(
        Config(builder="command", builder_command="unused"),
        builder=CommandBuilder("sh -c 'echo model not found >&2; exit 1'"),
    )

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    assert result["status"] == "builder_failed"
    assert "model not found" in result["builder_output"]
    assert "failed before making changes" in result["next_steps"]


def test_worktree_is_outside_the_repository(repo, runner, fake, tmp_path):
    fake({"calc.py": FIXED})

    result = runner.delegate(repo, TASK, TEST_COMMAND)

    worktree = result["worktree"]
    assert worktree.startswith(str(tmp_path / "cache" / "understudy" / "worktrees"))
    assert worktree.endswith(f"project-{result['id']}")
    assert ".git" not in worktree.split("/")


def test_failed_revision_rolls_back_to_passing_version(repo, runner, fake):
    fake({"calc.py": FIXED}, {"calc.py": STILL_BUGGY})
    delegation_id = runner.delegate(repo, TASK, TEST_COMMAND)["id"]

    result = runner.revise(repo, delegation_id, "Make it faster.")

    assert result["status"] == "revision_failed"
    assert result["rounds_used"] == 4
    assert "+    return a + b" in result["diff"]  # the passing version is kept
    assert "assert 6 == 5" in result["test_output"]  # but the failure is shown
    assert "rolled back" in result["next_steps"]
    assert runner.accept(repo, delegation_id)["status"] == "merged"
    assert (repo / "calc.py").read_text() == FIXED

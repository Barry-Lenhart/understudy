"""Call the MCP tool functions the way the server does."""

import sys

import anyio
import pytest
from conftest import FAKE_BUILDER, FIXED, TEST_COMMAND
from mcp.server.mcpserver.exceptions import ToolError

from understudy import server


@pytest.fixture
def env(monkeypatch, fake):
    monkeypatch.setenv("UNDERSTUDY_BUILDER", "command")
    monkeypatch.setenv(
        "UNDERSTUDY_BUILDER_COMMAND", f"{sys.executable} {FAKE_BUILDER} {{prompt_file}}"
    )
    fake({"calc.py": FIXED})


def test_tools_are_registered():
    names = {tool.name for tool in anyio.run(server.mcp.list_tools)}
    assert names == {"delegate", "revise", "accept", "discard", "status"}


def test_delegate_and_accept_through_tools(repo, env):
    result = anyio.run(lambda: server.delegate("Fix add().", TEST_COMMAND, str(repo)))
    assert result["status"] == "tests_passed"

    listed = anyio.run(lambda: server.status(str(repo)))
    assert [d["id"] for d in listed] == [result["id"]]

    merged = anyio.run(lambda: server.accept(result["id"], "Fix add()", str(repo)))
    assert merged["status"] == "merged"
    assert (repo / "calc.py").read_text() == FIXED


def test_errors_become_tool_errors(tmp_path, env):
    with pytest.raises(ToolError, match="not inside a git repository"):
        anyio.run(lambda: server.delegate("Fix add().", "", str(tmp_path)))


def test_bad_config_becomes_tool_error(repo, monkeypatch):
    monkeypatch.setenv("UNDERSTUDY_MAX_ROUNDS", "zero")
    with pytest.raises(ToolError, match="must be a whole number"):
        anyio.run(lambda: server.status(str(repo)))

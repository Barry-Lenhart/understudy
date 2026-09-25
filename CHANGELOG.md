# Changelog

## 0.1.0 (unreleased)

First release.

- MCP tools `delegate`, `revise`, `accept`, `discard` and `status`.
- Work happens in an isolated git worktree; tests run after each attempt and failures are fed back to the model.
- OpenCode builder (local models via Ollama) and a generic `command` builder.
- Warnings when a change deletes existing tests or functions.
- A failed `revise` rolls back to the last passing version.

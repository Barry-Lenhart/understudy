"""A stand-in for a local model, used by the tests.

Usage: python fake_builder.py <prompt_file>

Environment:
  FAKE_BUILDER_SCRIPT  JSON list of steps; step N is applied on the Nth call.
                       Each step is {"files": {"relative/path": "content"}}.
                       Calls beyond the end of the list change nothing.
  FAKE_BUILDER_STATE   Directory for the call counter and the prompt log.
"""

import json
import os
import sys
from pathlib import Path

prompt = Path(sys.argv[1]).read_text(encoding="utf-8")
state = Path(os.environ["FAKE_BUILDER_STATE"])
state.mkdir(parents=True, exist_ok=True)
counter = state / "calls"
call = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(call + 1))
with (state / "prompts.log").open("a", encoding="utf-8") as log:
    log.write(f"=== call {call + 1} ===\n{prompt}\n")

steps = json.loads(os.environ.get("FAKE_BUILDER_SCRIPT", "[]"))
if call < len(steps):
    for name, content in steps[call].get("files", {}).items():
        path = Path.cwd() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

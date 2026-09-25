"""Persisted state for each delegated task, stored under .git/understudy/."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import UnderstudyError


@dataclass
class Round:
    number: int
    builder_ok: bool
    changed: bool
    tests_passed: bool | None
    test_output: str = ""
    builder_output: str = ""


@dataclass
class Delegation:
    id: str
    task: str
    test_command: str
    repo: str
    worktree: str
    branch: str
    base: str
    status: str = "building"
    rounds: list[Round] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @staticmethod
    def new_id() -> str:
        return secrets.token_hex(3)

    def save(self, state_dir: Path) -> None:
        path = state_dir / "state" / f"{self.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, state_dir: Path, delegation_id: str) -> Delegation:
        path = state_dir / "state" / f"{delegation_id}.json"
        if not delegation_id.isalnum() or not path.exists():
            raise UnderstudyError(
                f"no delegation with id {delegation_id!r}; call status() to list them"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        data["rounds"] = [Round(**r) for r in data.get("rounds", [])]
        return cls(**data)

    @classmethod
    def load_all(cls, state_dir: Path) -> list[Delegation]:
        paths = sorted((state_dir / "state").glob("*.json"))
        return [cls.load(state_dir, p.stem) for p in paths]

    def delete(self, state_dir: Path) -> None:
        (state_dir / "state" / f"{self.id}.json").unlink(missing_ok=True)

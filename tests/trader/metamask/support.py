"""A scripted stand-in for the ``mm`` CLI, and envelope helpers."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from oracle3_extras.trader.metamask.attribution import Attribution
from oracle3_extras.trader.metamask.client import AgentWalletClient

FAKE_MM = Path(__file__).with_name('fake_mm.py')


def ok(command: str, result: Any) -> dict[str, Any]:
    return {'ok': True, 'data': {'command': command, 'params': {}, 'result': result}}


def err(code: str, message: str = 'failed', hint: str = 'fix it') -> dict[str, Any]:
    return {'ok': False, 'error': {'code': code, 'message': message, 'hint': hint}}


class FakeMM:
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.scenario_path = root / 'scenario.json'
        self.log_path = root / 'calls.jsonl'
        self.responses: dict[str, Any] = {}
        self.executable = root / 'mm'
        self.executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_MM}" "$@"\n'
        )
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.log_path.touch()
        self._write()
        monkeypatch.setenv('FAKE_MM_SCENARIO', str(self.scenario_path))
        monkeypatch.setenv('FAKE_MM_LOG', str(self.log_path))
        monkeypatch.delenv('ORACLE3_ATTRIBUTION', raising=False)

    def _write(self) -> None:
        self.scenario_path.write_text(json.dumps({'responses': self.responses}))

    def respond(self, key: str, reply: Any) -> FakeMM:
        self.responses[key] = reply
        self._write()
        return self

    def help(self, key: str, text: str) -> FakeMM:
        return self.respond('help:' + key, text)

    def calls(
        self, prefix: str | None = None, *, include_help: bool = False
    ) -> list[dict[str, Any]]:
        rows = [
            json.loads(line)
            for line in self.log_path.read_text().splitlines()
            if line.strip()
        ]
        if not include_help:
            rows = [r for r in rows if '--help' not in r['argv']]
        if prefix is None:
            return rows
        words = prefix.split()
        return [r for r in rows if r['argv'][: len(words)] == words]

    def client(self, **kwargs: Any) -> AgentWalletClient:
        kwargs.setdefault('attribution', Attribution())
        return AgentWalletClient(executable=str(self.executable), **kwargs)

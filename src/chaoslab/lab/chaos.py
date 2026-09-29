# SPDX-License-Identifier: Apache-2.0
"""Chaos engine: injects and restores only the command lists authored in lesson files.

It never invents commands. It dispatches by ``type`` (link|config|service|churn), tracks the one
active chaos, refuses a double injection, and refuses disabled (unverified) options.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..lessons.models import ChaosOption
from .adapter import CommandResult, DeviceAdapter


class ChaosError(Exception):
    """Raised on an invalid chaos operation (double-inject, disabled option, nothing active)."""


class ChaosResult(BaseModel):
    chaos_id: str
    kind: str
    commands: list[str]
    results: list[CommandResult]


class ChaosEngine:
    """Runs authored inject/restore command lists against the lab adapter."""

    def __init__(self, adapter: DeviceAdapter) -> None:
        self.adapter = adapter
        self.active: ChaosOption | None = None

    @property
    def active_id(self) -> str | None:
        return self.active.id if self.active else None

    def inject(self, option: ChaosOption) -> ChaosResult:
        if self.active is not None:
            raise ChaosError(f"chaos '{self.active.id}' is already active; restore first")
        if not option.enabled:
            raise ChaosError(f"chaos '{option.id}' is disabled (unverified on the lab)")
        self._dispatch(option.type)
        self.adapter.on_inject(option.id)
        results = self.adapter.run_many(option.inject)
        self.active = option
        return ChaosResult(
            chaos_id=option.id, kind=option.type, commands=option.inject, results=results
        )

    def restore(self, option: ChaosOption | None = None) -> ChaosResult:
        target = option or self.active
        if target is None:
            raise ChaosError("no active chaos to restore")
        results = self.adapter.run_many(target.restore)
        self.adapter.on_restore(target.id)
        if self.active and self.active.id == target.id:
            self.active = None
        return ChaosResult(
            chaos_id=target.id, kind=target.type, commands=target.restore, results=results
        )

    def reset(self) -> None:
        """Forget any active chaos (used by a full lab reset)."""
        self.active = None
        self.adapter.reset()

    @staticmethod
    def _dispatch(chaos_type: str) -> None:
        if chaos_type not in ("link", "config", "service", "churn"):
            raise ChaosError(f"undispatchable chaos type: {chaos_type}")

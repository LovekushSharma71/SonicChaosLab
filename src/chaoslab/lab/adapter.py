# SPDX-License-Identifier: Apache-2.0
"""Device adapter: runs SONiC CLI / redis reads on lab nodes.

ALL shell and docker access is confined to this module and ``topology.py`` (PRODUCT.md §8.1).
Three adapters are provided: a deterministic ``MockAdapter`` (fixtures, no lab), a
``DockerAdapter`` (docker exec into containerlab nodes), and a thin SSH wrapper of the latter.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from ..settings import Settings

TARGETS = ("leaf1", "leaf2", "h1", "h2", "h3", "h4")
_EXEC_TIMEOUT = 30


class CommandResult(BaseModel):
    """The raw result of running one ``<target>: <command>`` spec on the lab."""

    target: str
    command: str
    raw: str
    exit_code: int = 0

    @property
    def spec(self) -> str:
        return f"{self.target}: {self.command}"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def parse_spec(spec: str) -> tuple[str, str]:
    """Split a ``<target>: <command>`` spec into (target, command)."""
    if ":" not in spec:
        raise ValueError(f"command spec missing target prefix: {spec!r}")
    target, command = spec.split(":", 1)
    target, command = target.strip(), command.strip()
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r} in spec {spec!r}")
    if not command:
        raise ValueError(f"empty command in spec {spec!r}")
    return target, command


def slugify_spec(spec: str) -> str:
    """Map a command spec to a stable fixture filename stem."""
    target, command = parse_spec(spec)
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{target}__{command}").strip("_")


def default_fixtures_dir() -> Path:
    """Resolve the mock fixtures directory from env or by walking up from CWD."""
    env = os.environ.get("CHAOSLAB_FIXTURES")
    if env:
        return Path(env)
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "tests" / "fixtures" / "mock"
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "tests" / "fixtures" / "mock"


class DeviceAdapter(ABC):
    """Runs command specs against the lab and returns raw output."""

    @abstractmethod
    def run(self, spec: str, timeout: int | None = None) -> CommandResult: ...

    def run_many(self, specs: list[str]) -> list[CommandResult]:
        return [self.run(spec) for spec in specs]

    def on_inject(self, chaos_id: str) -> None:
        """Hook called before chaos injection (mock uses it to switch scenario)."""

    def on_restore(self, chaos_id: str) -> None:
        """Hook called after chaos restore."""

    def reset(self) -> None:
        """Clear any transient adapter state (mock chaos scenario)."""


class MockAdapter(DeviceAdapter):
    """Returns canned fixture outputs; switches to per-chaos overrides when injected."""

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self.fixtures_dir = fixtures_dir or default_fixtures_dir()
        self.active_chaos: str | None = None

    def run(self, spec: str, timeout: int | None = None) -> CommandResult:
        target, command = parse_spec(spec)
        return CommandResult(target=target, command=command, raw=self._read(slugify_spec(spec)))

    def _read(self, slug: str) -> str:
        if self.active_chaos:
            override = self.fixtures_dir / self.active_chaos / f"{slug}.txt"
            if override.exists():
                return override.read_text()
        base = self.fixtures_dir / f"{slug}.txt"
        if base.exists():
            return base.read_text()
        return f"(mock: no fixture for {slug})\n"

    def on_inject(self, chaos_id: str) -> None:
        self.active_chaos = chaos_id

    def on_restore(self, chaos_id: str) -> None:
        if self.active_chaos == chaos_id:
            self.active_chaos = None

    def reset(self) -> None:
        self.active_chaos = None


class DockerAdapter(DeviceAdapter):
    """Runs commands via ``docker exec`` into containerlab nodes (optionally over SSH)."""

    def __init__(
        self, lab_name: str = "chaoslab", prefix: str = "clab-", ssh_host: str = ""
    ) -> None:
        self.lab_name = lab_name
        self.prefix = prefix
        self.ssh_host = ssh_host

    def _container(self, target: str) -> str:
        return f"{self.prefix}{self.lab_name}-{target}"

    def run(self, spec: str, timeout: int | None = None) -> CommandResult:
        target, command = parse_spec(spec)
        # sh, not bash: the alpine host containers ship no bash; sonic-vs has both.
        argv = ["docker", "exec", self._container(target), "sh", "-lc", command]
        if self.ssh_host:
            argv = ["ssh", self.ssh_host, shlex.join(argv)]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout or _EXEC_TIMEOUT, check=False
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return CommandResult(
                target=target, command=command, raw=f"(adapter error: {exc})", exit_code=1
            )
        return CommandResult(
            target=target,
            command=command,
            raw=(proc.stdout or "") + (proc.stderr or ""),
            exit_code=proc.returncode,
        )


def make_adapter(settings: Settings, fixtures_dir: Path | None = None) -> DeviceAdapter:
    """Build the adapter selected by settings.lab_mode."""
    if settings.lab_mode == "mock":
        return MockAdapter(fixtures_dir)
    ssh_host = settings.lab_host if settings.lab_mode == "ssh" else ""
    return DockerAdapter(ssh_host=ssh_host)

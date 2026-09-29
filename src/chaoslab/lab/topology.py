# SPDX-License-Identifier: Apache-2.0
"""Topology manager: containerlab deploy / verify / destroy.

Shell access is confined to this module and ``adapter.py`` (PRODUCT.md §8.1). Every operation
degrades gracefully with a clear message when docker or containerlab is unavailable, so the CLI
never hangs and ``make lab-*`` is safe to run anywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

CLAB = "containerlab"
LAB_NAME = "chaoslab"
LEAVES = ("leaf1", "leaf2")
_READY_TIMEOUT_S = 180
_POLL_INTERVAL_S = 5


def topo_file() -> Path:
    return Path(os.environ.get("CHAOSLAB_TOPO", "topo/chaoslab.clab.yml"))


def preflight() -> tuple[bool, str]:
    """Check that docker, containerlab, and the topology file are present."""
    if shutil.which("docker") is None:
        return False, "docker not found (install Docker to run the lab)"
    if shutil.which(CLAB) is None:
        return False, "containerlab not found (see topo/README.md; the lab is x86-only)"
    if not topo_file().exists():
        return False, f"topology file not found: {topo_file()}"
    return True, ""


def _run(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, check=False).returncode
    except FileNotFoundError as exc:
        print(f"command not available: {exc}")
        return 1


def deploy() -> int:
    """Deploy the topology and poll until both leafs answer the SONiC CLI."""
    ok, message = preflight()
    if not ok:
        print(f"Cannot deploy: {message}")
        return 1
    code = _run([CLAB, "deploy", "-t", str(topo_file())])
    if code != 0:
        return code
    return 0 if wait_ready() else 1


def destroy() -> int:
    """Destroy the topology if containerlab is present (idempotent)."""
    if shutil.which(CLAB) is None:
        print("containerlab not found; nothing to destroy")
        return 0
    if not topo_file().exists():
        print("topology file not found; nothing to destroy")
        return 0
    return _run([CLAB, "destroy", "-t", str(topo_file()), "--cleanup"])


def node_ready(node: str) -> bool:
    container = f"clab-{LAB_NAME}-{node}"
    try:
        proc = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", "show version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def wait_ready(timeout: int = _READY_TIMEOUT_S) -> bool:
    """Poll until both leafs respond to the SONiC CLI, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(node_ready(node) for node in LEAVES):
            print("Both leafs are answering the SONiC CLI.")
            return True
        print("Waiting for leafs to come up...")
        time.sleep(_POLL_INTERVAL_S)
    print("Timed out waiting for leafs.")
    return False


def status() -> int:
    """Print containerlab inspect output and per-node readiness."""
    ok, message = preflight()
    if not ok:
        print(f"Lab not available: {message}")
        return 1
    _run([CLAB, "inspect", "-t", str(topo_file())])
    for node in LEAVES:
        print(f"  {node}: {'ready' if node_ready(node) else 'not ready'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    action = args[0] if args else "status"
    return {"up": deploy, "down": destroy, "status": status}.get(action, status)()


if __name__ == "__main__":
    raise SystemExit(main())

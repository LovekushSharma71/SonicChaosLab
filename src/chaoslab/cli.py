# SPDX-License-Identifier: Apache-2.0
"""The ``chaoslab`` CLI: a thin client over the orchestrator (no business logic here)."""

from __future__ import annotations

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .core import engine
from .core.orchestrator import Orchestrator
from .lab import topology
from .lab.chaos import ChaosError
from .llm import model
from .settings import Settings, load_env, load_settings, set_value, settings_dir

app = typer.Typer(add_completion=False, help="SONiC ChaosLab — learn networking by breaking it.")
config_app = typer.Typer(help="Switch configuration (CONFIG_DB view).")
app.add_typer(config_app, name="config")
console = Console()

TOPOLOGY_DIAGRAM = """\
h1 ── leaf1 ══════ leaf2 ── h4
        |            |
        h2           h3

leaf1: sonic-vs, AS 65001   (2 parallel /31 eBGP links)   leaf2: sonic-vs, AS 65002
h1/h2/h3/h4: alpine hosts on access VLANs
"""


def _load() -> Settings:
    load_env()
    return load_settings()


# --------------------------------------------------------------------- simple commands
@app.command()
def version() -> None:
    """Show version info."""
    console.print(f"SONiC ChaosLab {__version__}")


@app.command()
def status() -> None:
    """One-shot health: provider reachable? lab deployed? nodes ready?"""
    settings = _load()
    table = Table(title="chaoslab status", show_header=False)
    table.add_row(
        "provider",
        f"{settings.provider} ({'reachable' if model.provider_reachable(settings) else 'no key'})",
    )
    table.add_row("lab mode", settings.lab_mode)
    if settings.lab_mode == "mock":
        table.add_row("lab", "mock (no containerlab needed)")
    else:
        ok, message = topology.preflight()
        table.add_row("lab", "ready" if ok else message)
        if ok:
            for node in topology.LEAVES:
                table.add_row(f"  {node}", "ready" if topology.node_ready(node) else "not ready")
    console.print(table)


@app.command(name="topology")
def topology_() -> None:
    """Topology info: nodes, links, IPs/ASNs + diagram."""
    console.print(Panel(TOPOLOGY_DIAGRAM, title="topology"))


@app.command()
def up() -> None:
    """Deploy the containerlab topology (polls until both leafs answer)."""
    raise typer.Exit(topology.deploy())


@app.command()
def down() -> None:
    """Destroy the containerlab topology (graceful; idempotent)."""
    raise typer.Exit(topology.destroy())


@app.command()
def reset() -> None:
    """Restore the lab baseline (clears active chaos)."""
    settings = _load()
    if settings.lab_mode == "mock":
        console.print("Lab reset: mock adapter has no persistent state.")
        return
    console.print("Lab reset: re-apply baseline configs and 'config interface startup' on links.")


@app.command()
def transcript() -> None:
    """Export the current/last session transcript."""
    files = sorted(settings_dir().glob("transcript-*.txt"))
    if not files:
        console.print("No transcript found yet. Run a lesson with 'chaoslab select'.")
        return
    console.print(files[-1].read_text())


@app.command()
def settings(action: str = "list", key: str = "", value: str = "") -> None:
    """App settings: list | get <key> | set <key> <value>. API keys are env-only."""
    if action == "list":
        console.print_json(load_settings().model_dump_json())
    elif action == "get":
        console.print(getattr(load_settings(), key))
    elif action == "set":
        updated = set_value(key, value)
        console.print(f"set {key} = {getattr(updated, key)}")
    else:
        console.print("usage: chaoslab settings [list | get <key> | set <key> <value>]")


# --------------------------------------------------------------------- config commands
@config_app.command("get")
def config_get(node: str) -> None:
    """Dump a node's running configuration."""
    result = engine.config_get(node, _load())
    console.print(Panel(result.raw or "(empty)", title=f"{node} running config"))


@config_app.command("set")
def config_set(node: str, lines: list[str] = typer.Argument(...)) -> None:
    """Apply one or more SONiC 'config ...' set-family lines (allowlist-validated, atomic)."""
    settings = _load()
    preview = engine.config_set(node, lines, settings, confirm=False)
    if not preview.accepted:
        console.print(f"[red]Rejected line:[/red] {preview.rejected_line}")
        raise typer.Exit(1)
    console.print(Panel("\n".join(lines), title=f"apply to {node}?"))
    if not questionary.confirm("Apply this batch?").ask():
        console.print("Aborted.")
        return
    applied = engine.config_set(node, lines, settings, confirm=True)
    for result in applied.applied:
        console.print(f"  {result.spec}")


@app.command()
def run(
    cmd: str = typer.Option(..., "-cmd", "--cmd", help="command or NL instruction"),
    lesson: str = typer.Option("", "-l", "--lesson"),
    no_explain: bool = typer.Option(False, "--no-explain"),
) -> None:
    """Experiment mode: run a read-only command on the live lab with a grounded explanation."""
    settings = _load()
    lesson_id = lesson or engine.load_last_lesson()
    if not lesson_id:
        console.print("No active lesson. Run 'chaoslab select' first (or pass --lesson).")
        raise typer.Exit(1)
    orch = engine.build_orchestrator(lesson_id, settings)
    result = orch.experiment(cmd, explain=not no_explain)
    if result.proposed_command and not result.executed:
        console.print(f"Proposed: [cyan]{result.proposed_command}[/cyan]")
        if questionary.confirm("Run it?").ask():
            result = orch.experiment(result.proposed_command, explain=not no_explain)
    if result.redirected:
        console.print(Panel(result.output, title="off-topic"))
        return
    if not result.executed:
        console.print(result.output)
        return
    console.print(Panel(result.output, title=result.command))
    if result.explanation:
        console.print(
            Panel(
                result.explanation,
                title="explanation" + (" (lesson notes)" if result.fallback else ""),
            )
        )


# --------------------------------------------------------------------- guided loop
@app.command()
def select(lesson_id: str = typer.Argument("")) -> None:
    """Lesson catalogue → guided loop. With an id, jumps straight in."""
    settings = _load()
    lessons = engine.catalogue()
    if not lesson_id:
        choice = questionary.select(
            "Choose a lesson",
            choices=[f"{lesson.id} — {lesson.title}" for lesson in lessons],
        ).ask()
        if not choice:
            return
        lesson_id = choice.split(" — ", 1)[0]
    try:
        orch = engine.build_orchestrator(lesson_id, settings)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    engine.save_last_lesson(lesson_id)
    _run_loop(orch)


def _nav(orch: Orchestrator) -> str:
    choice = questionary.select(
        "Next?", choices=["Continue", "← Back", "Quit"], default="Continue"
    ).ask()
    if choice is None or choice == "Quit":
        return "quit"
    if choice == "← Back":
        orch.step_index = max(0, orch.step_index - 1)
        return "back"
    orch.advance()
    return "continue"


def _run_loop(orch: Orchestrator) -> None:
    transcript_lines: list[str] = []
    while not orch.finished:
        step = orch.current_step
        console.rule(f"[bold]{step.title}[/bold]" + ("  (optional)" if step.optional else ""))
        transcript_lines.append(f"## {step.title}")
        action = "continue"
        if step.kind == "teach":
            console.print(Panel(orch.teach_text(), title=step.id))
            action = _nav(orch)
        elif step.kind == "observe":
            _render_observe(orch, transcript_lines)
            action = _nav(orch)
        elif step.kind == "qna":
            action = _qna_loop(orch, transcript_lines)
        elif step.kind == "chaos_select":
            action = _chaos_step(orch, transcript_lines)
        elif step.kind == "restore":
            _render_restore(orch, transcript_lines)
            action = _nav(orch)
        if action == "quit":
            console.print("Session paused. Resume with 'chaoslab select'.")
            break
    else:
        console.print(Panel("Lesson complete. Well done!", style="green"))
    _write_transcript(orch, transcript_lines)


def _render_observe(orch: Orchestrator, transcript_lines: list[str]) -> None:
    result = orch.observe()
    for output in result.outputs:
        console.print(f"[dim]$ {output.spec}[/dim]")
        console.print("\n".join(output.raw.splitlines()[:12]) or "(no output)")
    console.print(
        Panel(
            result.explanation, title="explanation" + (" (lesson notes)" if result.fallback else "")
        )
    )
    transcript_lines.append(result.explanation)


def _qna_loop(orch: Orchestrator, transcript_lines: list[str]) -> str:
    while True:
        suggestions = orch.suggested_questions()
        choices = [*suggestions, "Ask your own", "Run an experiment", "Continue", "← Back", "Quit"]
        pick = questionary.select(f"Questions ({orch.questions_left} left)", choices=choices).ask()
        if pick is None or pick == "Quit":
            return "quit"
        if pick == "← Back":
            orch.step_index = max(0, orch.step_index - 1)
            return "back"
        if pick == "Continue":
            orch.advance()
            return "continue"
        if pick == "Ask your own":
            text = questionary.text("Your question:").ask() or ""
            if text.strip():
                _stream_answer(orch, text, transcript_lines)
        elif pick == "Run an experiment":
            text = questionary.text("Command or instruction:").ask() or ""
            if text.strip():
                _render_experiment(orch, text)
        else:
            _stream_answer(orch, pick, transcript_lines)
        if orch.questions_left <= 0:
            console.print("[yellow]Question budget reached; moving on.[/yellow]")
            orch.advance()
            return "continue"


def _stream_answer(orch: Orchestrator, text: str, transcript_lines: list[str]) -> None:
    console.print(f"[bold cyan]Q:[/bold cyan] {text}")
    console.print("[bold cyan]A:[/bold cyan] ", end="")
    answer = ""
    for event in orch.stream_question(text):
        if event.kind == "token":
            console.print(event.text, end="")
            answer += event.text
        elif event.kind in ("redirect", "budget"):
            console.print(event.text)
        elif event.kind == "verdict":
            console.print(f"\n[dim]— {event.text}[/dim]")
    transcript_lines.append(f"Q: {text}\nA: {answer}")


def _render_experiment(orch: Orchestrator, text: str) -> None:
    result = orch.experiment(text)
    if result.proposed_command and not result.executed:
        console.print(f"Proposed: [cyan]{result.proposed_command}[/cyan]")
        if questionary.confirm("Run it?").ask():
            result = orch.experiment(result.proposed_command)
    if result.redirected:
        console.print(Panel(result.output, title="off-topic"))
    elif not result.executed:
        console.print(result.output)
    else:
        console.print(Panel("\n".join(result.output.splitlines()[:12]), title=result.command))
        if result.explanation:
            console.print(Panel(result.explanation, title="explanation"))


def _chaos_step(orch: Orchestrator, transcript_lines: list[str]) -> str:
    menu = orch.chaos_menu()
    choices = []
    for item in menu:
        label = f"[{item.risk}] {item.label}"
        choices.append(
            questionary.Choice(
                title=label if item.enabled else f"{label} (unverified)",
                value=item.id,
                disabled="unverified" if not item.enabled else None,
            )
        )
    choices.append(questionary.Choice(title="← Back", value="__back__"))
    pick = questionary.select("Break something (predict the impact first!)", choices=choices).ask()
    if pick is None:
        return "quit"
    if pick == "__back__":
        orch.step_index = max(0, orch.step_index - 1)
        return "back"
    try:
        outcome = orch.select_chaos(pick)
    except ChaosError as exc:
        console.print(f"[red]{exc}[/red]")
        return "continue"
    _render_diff(outcome.changed_facts)
    console.print(
        Panel(outcome.explanation, title="impact" + (" (lesson notes)" if outcome.fallback else ""))
    )
    transcript_lines.append(f"Injected {outcome.chaos_id}: {', '.join(outcome.changed_facts)}")
    orch.advance()
    return "continue"


def _render_diff(changed: list[str]) -> None:
    if not changed:
        console.print(
            "[dim]No changed facts detected (mock has no override for this option).[/dim]"
        )
        return
    table = Table(title="changed facts")
    table.add_column("before → after")
    for fact in changed:
        table.add_row(fact)
    console.print(table)


def _render_restore(orch: Orchestrator, transcript_lines: list[str]) -> None:
    outcome = orch.restore()
    if outcome.healed:
        console.print(Panel("Baseline restored and verified.", style="green", title="restore"))
    else:
        console.print(
            Panel("Residual changes:\n" + "\n".join(outcome.residual_changes), title="restore")
        )
    transcript_lines.append(f"Restore {outcome.chaos_id}: healed={outcome.healed}")


def _write_transcript(orch: Orchestrator, lines: list[str]) -> None:
    directory = settings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"transcript-{orch.lesson.id}.txt").write_text("\n\n".join(lines))


if __name__ == "__main__":
    app()

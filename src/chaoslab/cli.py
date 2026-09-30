# SPDX-License-Identifier: Apache-2.0
"""The ``chaoslab`` CLI: a thin client over the orchestrator (no business logic here)."""

from __future__ import annotations

from pathlib import Path

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

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="SONiC ChaosLab — learn networking by breaking it.",
)
config_app = typer.Typer(help="Switch configuration (CONFIG_DB view).")
app.add_typer(config_app, name="config")
console = Console()

KEY_BINDINGS = """\
In-session navigation (three levels: catalogue → lesson menu → step):
  ↑/↓ + Enter      select in any menu (lessons, steps, chaos options, questions)
  ↑ Lesson menu    up one level from a step; Back in the lesson menu → catalogue
  ← Previous step  one step back inside the lesson
  Continue         advance to the next step
  Quit             save session state and return to the shell (chaoslab lessons resumes)
  Ctrl-C           same as Quit
"""

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
def help(ctx: typer.Context) -> None:
    """Full command reference + in-session key bindings."""
    console.print(ctx.parent.get_help() if ctx.parent else ctx.get_help())
    console.print(Panel(KEY_BINDINGS, title="key bindings"))


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
    """Restore the lab baseline: re-apply bound configs, startup ports, clear session state."""
    settings = _load()
    engine.clear_session_state()
    if settings.lab_mode == "mock":
        console.print("Lab reset: mock adapter has no persistent state; session state cleared.")
        return
    from .lab.adapter import make_adapter

    adapter = make_adapter(settings)
    console.print("Re-applying baselines (CONFIG_DB merge + FRR + port startup)...")
    if topology.apply_baseline(adapter):
        console.print("Lab baseline restored.")
    else:
        console.print("[red]Reset finished with errors — check with 'chaoslab status'.[/red]")
        raise typer.Exit(1)


@app.command()
def transcript() -> None:
    """Export the current/last session transcript."""
    files = sorted(settings_dir().glob("transcript-*.txt"))
    if not files:
        console.print("No transcript found yet. Run a lesson with 'chaoslab lessons'.")
        return
    console.print(files[-1].read_text())


@app.command()
def settings(
    action: str = typer.Argument("list"),
    key: str = typer.Argument(""),
    value: str = typer.Argument(""),
) -> None:
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
def config_set(
    node: str,
    lines: list[str] = typer.Argument(None),
    file: str = typer.Option("", "--file", help="read one command per line from a file"),
) -> None:
    """Apply one or more SONiC 'config ...' set-family lines (allowlist-validated, atomic)."""
    settings = _load()
    batch = list(lines or [])
    if file:
        batch = [line.strip() for line in Path(file).read_text().splitlines() if line.strip()]
    if not batch:
        console.print("Paste config lines, one per line; end with a blank line:")
        while True:
            entry = questionary.text("").ask()
            if not entry or not entry.strip():
                break
            batch.append(entry.strip())
    if not batch:
        console.print("Nothing to apply.")
        raise typer.Exit(1)
    preview = engine.config_set(node, batch, settings, confirm=False)
    if not preview.accepted:
        console.print(f"[red]Rejected line:[/red] {preview.rejected_line} (whole batch refused)")
        raise typer.Exit(1)
    console.print(Panel("\n".join(batch), title=f"apply to {node}?"))
    if not questionary.confirm("Apply this batch?").ask():
        console.print("Aborted.")
        return
    applied = engine.config_set(node, batch, settings, confirm=True)
    for result in applied.applied:
        console.print(f"  {result.spec}")
    console.print("[dim]Batch logged to the session transcript.[/dim]")


@app.command()
def run(
    cmd: str = typer.Option(..., "-cmd", "--cmd", help="command or NL instruction"),
) -> None:
    """Free experiment mode: run a read-only command on the lab (safety allowlist only)."""
    from .core.orchestrator import _PROPOSAL_MAP, _TARGET_PREFIX
    from .lab.adapter import make_adapter, parse_spec

    settings = _load()
    text = cmd.strip()
    if not _TARGET_PREFIX.match(text):
        low = text.lower()
        proposed = next(
            (command for keyword, command in _PROPOSAL_MAP if keyword in low), None
        )
        if proposed is None:
            console.print(
                'Use "<node>: <command>", e.g. chaoslab run -cmd "leaf1: show interfaces status".'
            )
            raise typer.Exit(1)
        console.print(f"Proposed: [cyan]{proposed}[/cyan]")
        if not questionary.confirm("Run it?").ask():
            return
        text = proposed
    try:
        parse_spec(text)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    # Safety allowlist is code-disabled until the real-LLM milestone (free experimentation).
    result = make_adapter(settings).run(text)
    console.print(Panel(result.raw or "(no output)", title=result.spec))


# --------------------------------------------------------------------- interactive shell
@app.command()
def shell() -> None:
    """Interactive shell: a SonicChaosLab> prompt that accepts every chaoslab command."""
    import shlex

    import click
    from typer.main import get_command

    root = get_command(app)
    console.print(
        "SONiC ChaosLab shell — type a command ('help' lists them, 'lessons' opens the "
        "catalogue, 'exit' leaves). The 'chaoslab' prefix is optional."
    )
    while True:
        try:
            line = input("SonicChaosLab> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            continue
        if tokens and tokens[0] == "chaoslab":  # tolerate the full command form
            tokens = tokens[1:]
        if not tokens:
            continue
        if tokens[0] in ("exit", "quit"):
            break
        if tokens[0] == "shell":
            console.print("Already in the shell.")
            continue
        try:
            root.main(args=tokens, prog_name="chaoslab", standalone_mode=False)
        except click.ClickException as exc:
            exc.show()
        except (click.exceptions.Exit, SystemExit):
            pass
        except KeyboardInterrupt:
            console.print("[dim](interrupted)[/dim]")
        except Exception as exc:  # the shell itself must never die (§4: CLI can never hang)
            console.print(f"[red]{exc}[/red]")


# --------------------------------------------------------------------- guided loop
@app.command()
def lessons() -> None:
    """Lesson catalogue → guided loop (Back inside a lesson returns here)."""
    _lessons_entry("")


@app.command()
def lesson(lesson_id: str = typer.Argument(...)) -> None:
    """Jump straight into one lesson's guided loop by id."""
    _lessons_entry(lesson_id)


def _lessons_entry(lesson_id: str) -> None:
    settings = _load()
    catalogue = engine.catalogue()
    while True:
        if not lesson_id:
            choice = questionary.select(
                "Choose a lesson — [id] title (difficulty); 'lesson <id>' jumps straight in",
                choices=[
                    questionary.Choice(
                        title=f"[{item.id}]  {item.title}  ({item.lesson.difficulty})",
                        value=item.id,
                    )
                    for item in catalogue
                ]
                + [questionary.Choice(title="Quit", value="__quit__")],
            ).ask()
            if choice is None or choice == "__quit__":
                return
            lesson_id = choice
        try:
            orch = engine.build_orchestrator(lesson_id, settings)
        except KeyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        engine.save_last_lesson(lesson_id)
        if _lesson_flow(orch) != "catalogue":
            return
        lesson_id = ""  # Back from the lesson menu → catalogue


def _lesson_flow(orch: Orchestrator) -> str:
    """Lesson menu ↔ step loop. Returns 'catalogue' (Back), 'quit', or 'finished'."""
    transcript_lines: list[str] = []
    action = "menu"
    saved = engine.load_session_state(orch.lesson.id)
    if saved and 0 < saved["step_index"] < len(orch.lesson.steps):
        resume = questionary.confirm(
            f"Resume where you left off (step {saved['step_index'] + 1})?"
        ).ask()
        if resume:
            orch.step_index = saved["step_index"]
            orch.questions_used = saved["questions_used"]
            orch.redirects_used = saved["redirects_used"]
            action = _run_loop(orch, transcript_lines)
    while action == "menu":
        picked = _step_menu(orch)
        if picked == "__back__":
            action = "catalogue"
            break
        if picked is None or picked == "__quit__":
            engine.save_session_state(orch)
            console.print("Session paused. Resume with 'chaoslab lessons'.")
            action = "quit"
            break
        orch.step_index = 0 if picked == -1 else picked
        action = _run_loop(orch, transcript_lines)
    _write_transcript(orch, transcript_lines)
    return action


def _step_menu(orch: Orchestrator) -> int | str | None:
    """Sublesson menu (§5): pick a step / chaos scenario, go back to the catalogue, or quit."""
    choices: list[questionary.Choice] = [
        questionary.Choice(title="▶ Start from the beginning", value=-1)
    ]
    for index, step in enumerate(orch.lesson.steps):
        marker = "○" if step.optional else "●"
        choices.append(
            questionary.Choice(title=f"{marker} {step.title} [{step.kind}]", value=index)
        )
    choices.append(questionary.Choice(title="← Back (catalogue)", value="__back__"))
    choices.append(questionary.Choice(title="Quit", value="__quit__"))
    return questionary.select(f"{orch.lesson.title} — start at", choices=choices).ask()


def _nav(orch: Orchestrator) -> str:
    choice = questionary.select(
        "Next?",
        choices=["Continue", "← Previous step", "↑ Lesson menu", "Quit"],
        default="Continue",
    ).ask()
    if choice is None or choice == "Quit":
        return "quit"
    if choice == "← Previous step":
        orch.step_index = max(0, orch.step_index - 1)
        return "back"
    if choice == "↑ Lesson menu":
        return "menu"
    orch.advance()
    return "continue"


def _run_loop(orch: Orchestrator, transcript_lines: list[str] | None = None) -> str:
    """Run steps until the lesson finishes or the user quits / returns to the lesson menu."""
    transcript_lines = [] if transcript_lines is None else transcript_lines
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
            engine.save_session_state(orch)
            console.print("Session paused. Resume with 'chaoslab lessons'.")
            return "quit"
        if action == "menu":
            return "menu"
    engine.clear_session_state()
    console.print(Panel("Lesson complete. Well done!", style="green"))
    return "finished"


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
        choices = [
            *suggestions,
            "Ask your own",
            "Run an experiment",
            "Continue",
            "← Previous step",
            "↑ Lesson menu",
            "Quit",
        ]
        pick = questionary.select(f"Questions ({orch.questions_left} left)", choices=choices).ask()
        if pick is None or pick == "Quit":
            return "quit"
        if pick == "← Previous step":
            orch.step_index = max(0, orch.step_index - 1)
            return "back"
        if pick == "↑ Lesson menu":
            return "menu"
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
        if 0 < orch.redirect_cap <= orch.redirects_used:
            console.print("[yellow]Too many off-topic inputs for this step; moving on.[/yellow]")
            orch.advance()
            return "continue"
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
    choices.append(questionary.Choice(title="← Previous step", value="__prev__"))
    choices.append(questionary.Choice(title="↑ Lesson menu", value="__menu__"))
    choices.append(questionary.Choice(title="Quit", value="__quit__"))
    pick = questionary.select("Break something (predict the impact first!)", choices=choices).ask()
    if pick is None or pick == "__quit__":
        return "quit"
    if pick == "__prev__":
        orch.step_index = max(0, orch.step_index - 1)
        return "back"
    if pick == "__menu__":
        return "menu"
    prediction = questionary.text("Your prediction — what do you expect to change?").ask() or ""
    if prediction.strip():
        transcript_lines.append(f"Prediction for {pick}: {prediction.strip()}")
    try:
        outcome = orch.select_chaos(pick)
    except ChaosError as exc:
        console.print(f"[red]{exc}[/red]")
        return "continue"
    _render_diff(outcome.changed_facts)
    if prediction.strip():
        console.print(Panel(prediction.strip(), title="your prediction — compare against the diff"))
    console.print(
        Panel(outcome.explanation, title="impact" + (" (lesson notes)" if outcome.fallback else ""))
    )
    transcript_lines.append(f"Injected {outcome.chaos_id}: {', '.join(outcome.changed_facts)}")
    orch.advance()
    return "continue"


def _render_diff(changed: list[str]) -> None:
    if not changed:
        console.print("[dim]No changed facts detected between the before/after snapshots.[/dim]")
        return
    table = Table(title="changed facts")
    table.add_column("before → after")
    for fact in changed:
        table.add_row(fact)
    console.print(table)


def _render_restore(orch: Orchestrator, transcript_lines: list[str]) -> None:
    outcome = orch.restore()
    timing = f" (recovered in {outcome.recovery_seconds:g} s)" if outcome.chaos_id else ""
    if outcome.healed:
        console.print(
            Panel(f"Baseline restored and verified.{timing}", style="green", title="restore")
        )
    else:
        console.print(
            Panel(
                "Residual changes:\n" + "\n".join(outcome.residual_changes) + timing,
                title="restore",
            )
        )
    transcript_lines.append(
        f"Restore {outcome.chaos_id}: healed={outcome.healed} in {outcome.recovery_seconds:g}s"
    )


def _write_transcript(orch: Orchestrator, lines: list[str]) -> None:
    directory = settings_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"transcript-{orch.lesson.id}.txt").write_text("\n\n".join(lines))


if __name__ == "__main__":
    app()

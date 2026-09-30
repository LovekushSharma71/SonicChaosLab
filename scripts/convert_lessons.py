#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Mechanically convert lessons/<id>.md into lessons/<id>/{lesson.yaml,teach.md,card.yaml}.

Structure is taken from each lesson's Machine Summary JSON; prose content is taken verbatim
from the Markdown sections. Nothing is paraphrased. The generated files, plus a regenerated
curriculum.yaml and schema.yaml, are validated with the loader. Any violation fails loudly
with the offending file and section.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from chaoslab.lessons import loader  # noqa: E402
from chaoslab.lessons.schema import SCHEMA_YAML  # noqa: E402

LESSON_ORDER = ["switches_explained", "inside_sonic", "bgp_reconvergence", "mtu_mismatch"]
FACT_ENUM = [
    "interface_status",
    "mac_table",
    "lldp_neighbors",
    "vlan_membership",
    "bgp_neighbors",
    "route_count",
    "ping_loss",
    "redis_keys",
    "propagation_lag",
]
TARGET_RE = re.compile(r"^(leaf1|leaf2|h1|h2|h3|h4):")
DASH = r"[—–]"


class ConversionError(Exception):
    """Raised when a lesson Markdown file cannot be converted mechanically."""


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def paren_split(text: str) -> list[str]:
    """Split a prose subtopic list on its top-level delimiter, ignoring delimiters in parens.

    Lessons use ';' or ',' inconsistently as the separator, with the other appearing inside
    parenthetical asides. Prefer ';' when present, else split on ',' at paren-depth zero.
    """
    delimiter = ";" if ";" in text else ","
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == delimiter and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def split_top_sections(md: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        match = re.match(r"^##(?!#)\s+(.+)$", line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = match.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def split_sub_sections(body: str) -> dict[str, str]:
    subs: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^###(?!#)\s+(.+)$", line)
        if match:
            if current is not None:
                subs[current] = "\n".join(buf).strip()
            current = match.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        subs[current] = "\n".join(buf).strip()
    return subs


def find_sub(subs: dict[str, str], prefix: str, lesson_id: str) -> str:
    for key, value in subs.items():
        if key.lower().startswith(prefix.lower()):
            return value
    raise ConversionError(f"{lesson_id}: missing card subsection starting '{prefix}'")


def extract_title(md: str, lesson_id: str) -> str:
    for line in md.splitlines():
        if line.startswith("# Lesson:"):
            rest = line[len("# Lesson:") :].strip()
            parts = re.split(DASH, rest, maxsplit=1)
            if len(parts) != 2:
                raise ConversionError(f"{lesson_id}: cannot parse title from H1")
            return parts[1].strip()
    raise ConversionError(f"{lesson_id}: no '# Lesson:' header")


def extract_machine_summary(sections: dict[str, str], lesson_id: str) -> dict:
    body = sections.get("Machine Summary")
    if not body:
        raise ConversionError(f"{lesson_id}: no Machine Summary section")
    match = re.search(r"```json\s*(.+?)```", body, re.DOTALL)
    if not match:
        raise ConversionError(f"{lesson_id}: no JSON block in Machine Summary")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ConversionError(f"{lesson_id}: bad Machine Summary JSON: {exc}") from exc


def parse_meta(body: str, lesson_id: str) -> dict:
    difficulty = re.search(r"\*\*difficulty:\*\*\s*(\w+)", body)
    if not difficulty:
        raise ConversionError(f"{lesson_id}: no difficulty in Meta")
    branch = re.search(r"branch\s*\*\*(\w+)\*\*", body)
    requires: list[str] = []
    requires_line = re.search(r"\*\*requires:\*\*\s*(.+)", body)
    if requires_line:
        requires = re.findall(r"`([a-z0-9_]+)`", requires_line.group(1))
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s<>()]+", body):
        url = match.group(0)
        if url not in urls:
            urls.append(url)
    return {
        "difficulty": difficulty.group(1),
        "branch": branch.group(1) if branch else "unknown",
        "requires": requires,
        "source_urls": urls,
    }


def parse_step_titles(body: str) -> dict[str, str]:
    titles: dict[str, str] = {}
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        titles[cells[1]] = cells[3]
    return titles


def parse_vocabulary(commands_body: str, lesson_id: str) -> list[str]:
    subs = split_sub_sections(commands_body)
    vocab_body = None
    for key, value in subs.items():
        if key.lower().startswith("vocabulary"):
            vocab_body = value
            break
    if vocab_body is None:
        raise ConversionError(f"{lesson_id}: no Vocabulary subsection in Commands")
    match = re.search(r"```\s*(.+?)```", vocab_body, re.DOTALL)
    if not match:
        raise ConversionError(f"{lesson_id}: no code block in Vocabulary")
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def _region(text: str, start: str, ends: list[str]) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    i += len(start)
    end = len(text)
    for marker in ends:
        j = text.find(marker, i)
        if 0 <= j < end:
            end = j
    return text[i:end]


def _commands(region: str | None) -> list[str]:
    if region is None:
        return []
    out: list[str] = []
    for match in re.finditer(r"`([^`]+)`", region):
        command = match.group(1).strip()
        if TARGET_RE.match(command):
            out.append(command)
    return out


def parse_chaos_options(body: str, lesson_id: str) -> list[dict]:
    chunks = re.split(r"(?m)^---+\s*$", body)
    options: list[dict] = []
    for chunk in chunks:
        header = re.search(
            r"id:\s*`(c_[a-z0-9_]+)`\s*" + DASH + r'\s*"([^"]+)"',
            chunk,
        )
        if not header:
            continue
        chaos_id, label = header.group(1), header.group(2)
        type_match = re.search(r"\*\*type:\*\*\s*(link|config|service|churn)", chunk)
        risk_match = re.search(r"\*\*risk:\*\*\s*(low|medium|high)", chunk)
        enabled_match = re.search(r"\*\*enabled:\*\*\s*(true|false)", chunk)
        if not (type_match and risk_match and enabled_match):
            raise ConversionError(f"{lesson_id}/{chaos_id}: missing type/risk/enabled")

        inject = _commands(_region(chunk, "**inject:**", ["\n- **restore:"]))
        restore = _commands(_region(chunk, "**restore:**", ["\n- **expected effects"]))
        if not inject or not restore:
            raise ConversionError(f"{lesson_id}/{chaos_id}: empty inject or restore command list")

        expected = _region(
            chunk,
            "**expected effects (words):**",
            ["\n- **plan-B", "\n- **injection-failed"],
        )
        if not expected:
            raise ConversionError(f"{lesson_id}/{chaos_id}: no expected effects")
        plan_b = _region(chunk, "**plan-B variant:**", ["\n- **injection-failed"])

        option = {
            "id": chaos_id,
            "label": label,
            "type": type_match.group(1),
            "inject": inject,
            "restore": restore,
            "expected_effects": [collapse(expected)],
            "risk": risk_match.group(1),
            "enabled": enabled_match.group(1) == "true",
        }
        if plan_b:
            option["plan_b"] = collapse(plan_b)
        options.append(option)
    if not options:
        raise ConversionError(f"{lesson_id}: no chaos options parsed")
    return options


def parse_observe(body: str) -> dict:
    filtered = "\n".join(line for line in body.splitlines() if "Parsers available" not in line)
    facts = [token for token in FACT_ENUM if token in filtered]
    measure_recovery = "| yes" in filtered
    return {"facts": facts, "measure_recovery": measure_recovery}


def parse_question_bank(qna_body: str, qna_ids: list[str], lesson_id: str) -> dict[str, list[str]]:
    blocks: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in qna_body.splitlines():
        match = re.match(r"^####\s+(\S+)", line)
        if match:
            if current is not None:
                blocks[current] = "\n".join(buf)
            current = match.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        blocks[current] = "\n".join(buf)

    result: dict[str, list[str]] = {}
    for qid in qna_ids:
        if qid not in blocks:
            raise ConversionError(f"{lesson_id}: no question bank block for '{qid}'")
        starred = [
            match.group(1).strip()
            for line in blocks[qid].splitlines()
            if (match := re.match(r"^\d+\.\s*★\s*(.+)$", line))
        ]
        if len(starred) != 3:
            raise ConversionError(
                f"{lesson_id}/{qid}: expected 3 starred questions, found {len(starred)}"
            )
        result[qid] = starred
    return result


def _clean_quoted(text: str) -> str:
    return text.replace("**", "").strip().strip('"“”').strip()


def parse_card(
    card_body: str, lesson_id: str, chaos_ids: list[str], source_urls: list[str]
) -> dict:
    subs = split_sub_sections(card_body)

    key_concepts = [
        match.group(1).strip()
        for line in find_sub(subs, "Key concepts", lesson_id).splitlines()
        if (match := re.match(r"^\d+\.\s+(.*)$", line))
    ]

    command_field_meanings: dict[str, dict[str, str]] = {}
    for line in find_sub(subs, "Command field meanings", lesson_id).splitlines():
        if not line.strip().startswith("- **"):
            continue
        backtick = re.search(r"`([^`]+)`", line)
        if not backtick:
            continue
        command = backtick.group(1).strip()
        parts = re.split(DASH, line, maxsplit=1)
        meaning = collapse(parts[1]) if len(parts) == 2 else ""
        command_field_meanings[command] = {"meaning": meaning}

    expected: dict[str, str] = {}
    for line in find_sub(subs, "Expected chaos effects", lesson_id).splitlines():
        match = re.match(r"^-\s+\*\*(c_[a-z0-9_]+):\*\*\s*(.*)$", line)
        if match:
            expected[match.group(1)] = collapse(match.group(2))

    misconceptions = []
    for line in find_sub(subs, "Misconceptions", lesson_id).splitlines():
        match = re.match(r"^\d+\.\s+(.*)$", line)
        if not match:
            continue
        parts = re.split(r"→", match.group(1))
        if len(parts) < 3:
            raise ConversionError(f"{lesson_id}: misconception missing two arrows: {line}")
        misconceptions.append(
            {
                "misconception": _clean_quoted(parts[0]),
                "why_wrong": parts[1].strip(),
                "correct_model": "→".join(parts[2:]).strip(),
            }
        )

    in_scope = paren_split(collapse(find_sub(subs, "In-scope subtopics", lesson_id)))
    out_of_scope = paren_split(collapse(find_sub(subs, "Out-of-scope", lesson_id)).rstrip("."))

    card = {
        "objective": collapse(find_sub(subs, "Objective", lesson_id)),
        "in_scope_subtopics": in_scope,
        "key_concepts": key_concepts,
        "command_field_meanings": command_field_meanings,
        "healthy_state_expectations": find_sub(subs, "Healthy-state expectations", lesson_id),
        "expected_chaos_effects": expected,
        "common_misconceptions": misconceptions,
        "out_of_scope": out_of_scope,
        "redirect_line": _clean_quoted(find_sub(subs, "Polite redirect line", lesson_id)),
    }
    if source_urls:
        card["source_urls"] = source_urls

    missing = set(chaos_ids) - set(expected)
    if missing:
        raise ConversionError(f"{lesson_id}: expected_chaos_effects missing {sorted(missing)}")
    return card


def convert_lesson(md_path: Path, lessons_dir: Path) -> str:
    lesson_id = md_path.stem
    md = md_path.read_text()
    sections = split_top_sections(md)

    machine = extract_machine_summary(sections, lesson_id)
    if machine.get("id") != lesson_id:
        raise ConversionError(f"{lesson_id}: Machine Summary id '{machine.get('id')}' mismatch")

    meta = parse_meta(sections.get("Meta", ""), lesson_id)
    step_titles = parse_step_titles(sections.get("Step Plan", ""))
    vocabulary = parse_vocabulary(sections.get("Commands", ""), lesson_id)
    chaos_options = parse_chaos_options(sections.get("Chaos Options", ""), lesson_id)
    observe = parse_observe(sections.get("Observe", ""))

    chaos_ids = [c["id"] for c in chaos_options]
    if chaos_ids != machine.get("chaos_ids"):
        raise ConversionError(f"{lesson_id}: parsed chaos ids differ from Machine Summary")
    enabled_ids = [c["id"] for c in chaos_options if c["enabled"]]
    if enabled_ids != machine.get("enabled_chaos"):
        raise ConversionError(f"{lesson_id}: enabled chaos ids differ from Machine Summary")

    steps = []
    for step in machine["steps"]:
        step_id = step["id"]
        if step_id not in step_titles:
            raise ConversionError(f"{lesson_id}: no Step Plan title for '{step_id}'")
        steps.append(
            {
                "id": step_id,
                "kind": step["kind"],
                "title": step_titles[step_id],
                "optional": not step["core"],
            }
        )

    ms_commands = machine["commands"]
    after_chaos = ms_commands["after_chaos"]
    per_step = {k: v for k, v in ms_commands.items() if k != "after_chaos"}
    first_observe = next(s["id"] for s in machine["steps"] if s["kind"] == "observe")
    commands = {
        "baseline": list(per_step[first_observe]),
        "after_chaos": after_chaos,
        "per_step": per_step,
        "vocabulary": vocabulary,
    }

    qna_ids = [s["id"] for s in machine["steps"] if s["kind"] == "qna"]
    suggested = parse_question_bank(sections.get("QnA", ""), qna_ids, lesson_id)

    lesson: dict = {
        "id": lesson_id,
        "title": extract_title(md, lesson_id),
        "difficulty": meta["difficulty"],
    }
    if meta["requires"]:
        lesson["requires"] = meta["requires"]
    lesson["teach_file"] = "./teach.md"
    lesson["card_file"] = "./card.yaml"
    lesson["steps"] = steps
    lesson["commands"] = commands
    lesson["chaos_options"] = chaos_options
    lesson["observe"] = observe
    lesson["qna"] = {"scope_keywords": machine["keywords"], "suggested_questions": suggested}
    lesson["meta"] = {
        "verified_on": f"UNVERIFIED — docker-sonic-vs:{meta['branch']}",
        "card_version": 1,
    }

    card = parse_card(sections.get("Knowledge Card", ""), lesson_id, chaos_ids, meta["source_urls"])
    teach_body = sections.get("Teach Sections", "")
    if not teach_body:
        raise ConversionError(f"{lesson_id}: no Teach Sections")

    out_dir = lessons_dir / lesson_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _dump_yaml(out_dir / "lesson.yaml", lesson)
    _dump_yaml(out_dir / "card.yaml", card)
    (out_dir / "teach.md").write_text(teach_body + "\n")
    return lesson_id


def _dump_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(
            data, sort_keys=False, allow_unicode=True, width=4096, default_flow_style=False
        )
    )


def main(lessons_dir: Path | None = None) -> int:
    lessons_dir = lessons_dir or (REPO_ROOT / "lessons")
    (lessons_dir / "schema.yaml").write_text(SCHEMA_YAML)

    converted: list[str] = []
    for lesson_id in LESSON_ORDER:
        md_path = lessons_dir / f"{lesson_id}.md"
        if not md_path.exists():
            raise ConversionError(f"missing lesson source: {md_path}")
        converted.append(convert_lesson(md_path, lessons_dir))
        print(f"converted {lesson_id}")

    curriculum = {
        "course": "SONiC ChaosLab core track",
        "version": 1,
        "lessons": [f"{lesson_id}/lesson.yaml" for lesson_id in LESSON_ORDER],
    }
    _dump_yaml(lessons_dir / "curriculum.yaml", curriculum)

    loaded = loader.load_all(lessons_dir)
    print(f"validated {len(loaded)} lessons against schema + cross-file rules")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ConversionError, loader.LessonValidationError) as error:
        print(f"CONVERSION FAILED: {error}", file=sys.stderr)
        raise SystemExit(1) from error

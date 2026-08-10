"""Skill loader.

Skills live as markdown files with frontmatter in skills/. They are kept as files, not
inlined strings, for two reasons: the future mobile-app agent loads the same files, and
they port into `.claude/skills/` unchanged if the brain moves to Claude on Model Garden.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config import SKILLS_DIR

# Load order is the order they are appended to the system prompt.
ORCHESTRATOR_SKILLS = ("voice-turns", "safety-callouts")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[2].lstrip("\n")


def load_skill(name: str, directory: Path | None = None) -> Skill:
    path = (directory or SKILLS_DIR) / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(raw)
    return Skill(
        name=meta.get("name", name),
        description=meta.get("description", ""),
        body=body.strip(),
        path=path,
    )


def load_skills(names: tuple[str, ...] = ORCHESTRATOR_SKILLS) -> list[Skill]:
    return [load_skill(name) for name in names]


def render_skills(skills: list[Skill]) -> str:
    """Render loaded skills for appending to a system prompt."""
    blocks = [
        "# Loaded skills\n"
        "These are operating rules, not suggestions. They override your defaults for style "
        "and for hazard handling."
    ]
    for skill in skills:
        blocks.append(f"\n<skill name=\"{skill.name}\">\n{skill.body}\n</skill>")
    return "\n".join(blocks)

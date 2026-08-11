"""Tiny YAML-frontmatter helpers shared by the packager (writes it) and the
plugin_spec validation check (reads it back to confirm what was written)."""

from __future__ import annotations

import yaml

_DELIM = "---"


class FrontmatterError(ValueError):
    pass


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _DELIM:
        raise FrontmatterError("file does not start with a '---' frontmatter block")
    try:
        end = lines[1:].index(_DELIM) + 1
    except ValueError as exc:
        raise FrontmatterError("frontmatter block is never closed with '---'") from exc

    raw_yaml = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    data = yaml.safe_load(raw_yaml) or {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return data, body


def render_frontmatter(data: dict, body: str) -> str:
    yaml_text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_text}\n---\n\n{body.strip()}\n"

"""Semver helpers for the packager.

A freshly generated plugin starts at `INITIAL_VERSION`. Regenerating against
updated data/bindings should bump the patch version by default; the caller
decides on a bigger bump (e.g. a new KPI landing in the pack = minor).
"""

from __future__ import annotations

import re

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

INITIAL_VERSION = "0.1.0"


class InvalidVersionError(ValueError):
    pass


def parse_version(version: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.match(version)
    if not match:
        raise InvalidVersionError(f"not a valid semver version: {version!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump(version: str, part: str = "patch") -> str:
    major, minor, patch = parse_version(version)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise InvalidVersionError(f"unknown version part: {part!r}; expected major/minor/patch")

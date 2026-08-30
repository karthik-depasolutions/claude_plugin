"""Fast script to fetch skills from GitHub repositories using the GitHub API."""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TARGET_DIR = Path("d:/claude-plugin-poc/collected_skills")
TARGET_DIR.mkdir(parents=True, exist_ok=True)

SKILL_SOURCES = [
    {"repo": "shubhamsaboo/awesome-llm-apps", "skill": "data-analyst"},
    {"repo": "claude-office-skills/skills", "skill": "data-analysis"},
    {"repo": "astronomer/agents", "skill": "analyzing-data"},
    {"repo": "bytedance/deer-flow", "skill": "data-analysis"},
    {"repo": "markdown-viewer/skills", "skill": "data-analytics"},
    {"repo": "davila7/claude-code-templates", "skill": "exploratory-data-analysis"},
    {"repo": "google/skills", "skill": "google-analytics-data-api-basics"},
    {"repo": "mindrally/skills", "skill": "data-analyst"},
]


def http_get_json(url: str):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/vnd.github.v3+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [API Error] {url} -> {e}", flush=True)
        return None


def fetch_raw_file(raw_url: str) -> str | None:
    req = urllib.request.Request(
        raw_url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"  [Raw fetch error] {raw_url} -> {e}", flush=True)
        return None


def process_source(source: dict):
    repo = source["repo"]
    skill_name = source["skill"]
    print(f"\n==========================================", flush=True)
    print(f"Scanning {repo} for skill '{skill_name}'...", flush=True)

    # 1. Get repo details (default branch)
    repo_data = http_get_json(f"https://api.github.com/repos/{repo}")
    if not repo_data:
        print(f"  Failed to get repo info for {repo}", flush=True)
        return

    default_branch = repo_data.get("default_branch", "main")
    print(f"  Default branch: {default_branch}", flush=True)

    # 2. Fetch git tree recursively
    tree_data = http_get_json(
        f"https://api.github.com/repos/{repo}/git/trees/{default_branch}?recursive=1"
    )
    if not tree_data or "tree" not in tree_data:
        print(f"  Failed to get git tree for {repo}", flush=True)
        return

    tree = tree_data["tree"]
    
    # 3. Find files relevant to this skill
    # Strategy: Find any path containing the skill name or in a skill directory
    matching_items = []
    for item in tree:
        path = item.get("path", "")
        # Check if item path contains the skill name
        path_lower = path.lower()
        skill_lower = skill_name.lower()
        
        if skill_lower in path_lower or f"skills/{skill_lower}" in path_lower:
            matching_items.append(item)

    if not matching_items:
        # Check if entire repo is the skill (e.g. SKILL.md in root)
        for item in tree:
            if item.get("path") in ["SKILL.md", "README.md"]:
                matching_items.append(item)

    print(f"  Found {len(matching_items)} potential matching files/dirs.", flush=True)

    # Destination directory for this skill
    # Prefix with repo owner to avoid folder collisions if multiple repos have the same skill name
    owner = repo.split("/")[0]
    dest_dir = TARGET_DIR / f"{owner}_{skill_name}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for item in matching_items:
        if item.get("type") == "blob":
            path = item["path"]
            raw_url = f"https://raw.githubusercontent.com/{repo}/{default_branch}/{path}"
            content = fetch_raw_file(raw_url)
            if content:
                # Relative file path inside the skill directory
                # If path was skills/data-analyst/SKILL.md -> SKILL.md
                rel_parts = path.split("/")
                if skill_name in rel_parts:
                    idx = rel_parts.index(skill_name)
                    subpath = Path(*rel_parts[idx + 1:]) if idx + 1 < len(rel_parts) else Path(rel_parts[-1])
                else:
                    subpath = Path(*rel_parts)
                
                target_file = dest_dir / subpath
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")
                print(f"  [SAVED] {path} -> {target_file}", flush=True)
                downloaded += 1

    if downloaded > 0:
        print(f"  [SUCCESS] {downloaded} files saved to {dest_dir}", flush=True)
    else:
        print(f"  [WARNING] No files could be downloaded for {skill_name} in {repo}", flush=True)


if __name__ == "__main__":
    for item in SKILL_SOURCES:
        process_source(item)
    print("\n==========================================", flush=True)
    print(f"All skills saved in: {TARGET_DIR.resolve()}", flush=True)

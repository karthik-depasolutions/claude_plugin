"""Script to fetch all curated top-tier data analytics and visualization skills."""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

TARGET_DIR = Path("d:/claude-plugin-poc/collected_skills")
TARGET_DIR.mkdir(parents=True, exist_ok=True)

CURATED_REPOS = [
    {"repo": "nimrodfisher/data-analytics-skills", "description": "31 Full-workflow data analytics skills"},
    {"repo": "adityawrk/analytics-with-claude-code", "description": "Production analytics engineering & SQL skills"},
    {"repo": "clamp-sh/analytics-skills", "description": "Clamp analytics diagnostic & experiment reading skills"},
    {"repo": "florianbonnet14/ThePowerOfAnalytics_ClaudeSkills", "description": "The Power of Analytics metric trees & cohort skills"},
    {"repo": "anthropics/knowledge-work-plugins", "description": "Anthropic official create-viz & data-visualization plugins"},
    {"repo": "aref-vc/tufte-claude-skill", "description": "Edward Tufte data visualization principles skill"},
    {"repo": "jkoets/Observable-Plot-Claude-Skill", "description": "Observable Plot exploratory visualization skill"},
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
        with urllib.request.urlopen(req, timeout=20) as resp:
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        return None


def download_repo_skills(repo_info: dict):
    repo = repo_info["repo"]
    desc = repo_info["description"]
    owner, repo_name = repo.split("/")
    
    print(f"\n==========================================", flush=True)
    print(f"Processing: {repo} ({desc})...", flush=True)

    repo_data = http_get_json(f"https://api.github.com/repos/{repo}")
    if not repo_data:
        print(f"  [SKIP] Repo {repo} not accessible or private.", flush=True)
        return

    default_branch = repo_data.get("default_branch", "main")
    print(f"  Default branch: {default_branch}", flush=True)

    tree_data = http_get_json(
        f"https://api.github.com/repos/{repo}/git/trees/{default_branch}?recursive=1"
    )
    if not tree_data or "tree" not in tree_data:
        print(f"  [SKIP] Tree not found for {repo}", flush=True)
        return

    tree = tree_data["tree"]
    
    # Identify skills: look for SKILL.md, .md files, prompts, or scripts
    # Destination root for this repo
    dest_repo_dir = TARGET_DIR / f"{owner}_{repo_name}"
    dest_repo_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for item in tree:
        if item.get("type") == "blob":
            path = item["path"]
            # Exclude large binaries, git internals, or packaging bloat
            if any(path.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".ico", ".pdf", ".zip"]):
                continue
            
            raw_url = f"https://raw.githubusercontent.com/{repo}/{default_branch}/{path}"
            content = fetch_raw_file(raw_url)
            if content is not None:
                target_file = dest_repo_dir / Path(path)
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(content, encoding="utf-8")
                saved_count += 1

    print(f"  [SUCCESS] {saved_count} files downloaded to {dest_repo_dir}", flush=True)


if __name__ == "__main__":
    for repo_info in CURATED_REPOS:
        download_repo_skills(repo_info)
    print("\n==========================================", flush=True)
    print(f"All curated skills saved in: {TARGET_DIR.resolve()}", flush=True)

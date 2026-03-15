#!/usr/bin/env python3
"""Verify which OSS-Fuzz projects are forked (including submodules) into the target org."""

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from shared._github import extract_urls, parse_github_nwo, repo_exists

_DEFAULT_OSS_FUZZ_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify OSS-Fuzz project forks exist on GitHub."
    )
    parser.add_argument(
        "--oss-fuzz-dir",
        default=str(_DEFAULT_OSS_FUZZ_DIR),
        help="Path to local oss-fuzz clone (default: parent of vuljector dir)",
    )
    parser.add_argument("--org", default="vuljector", help="Target GitHub org")
    parser.add_argument(
        "--show-ok",
        action="store_true",
        help="Also list fully forked projects",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Submodule check
# ---------------------------------------------------------------------------

def get_submodule_repo_names(org: str, repo_name: str) -> list[str]:
    """Return GitHub repo names for submodules declared in the fork's .gitmodules."""
    result = subprocess.run(
        ["gh", "api", f"repos/{org}/{repo_name}/contents/.gitmodules"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        content = base64.b64decode(data["content"]).decode()
    except (json.JSONDecodeError, KeyError, ValueError):
        return []

    github_re = re.compile(
        r"https?://github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?$", re.IGNORECASE
    )
    names = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("url"):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                m = github_re.match(parts[1].strip())
                if m:
                    names.append(m.group(1).split("/")[1])
    return names


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    projects_dir = Path(args.oss_fuzz_dir) / "projects"
    projects = sorted(p.name for p in projects_dir.iterdir() if p.is_dir())

    ok: list[str] = []
    not_forked: list[tuple[str, str]] = []
    partial: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    total = len(projects)
    for i, project in enumerate(projects, 1):
        print(f"  [{i}/{total}] {project} ...", end="\r", flush=True)

        all_urls, skip_reason = extract_urls(projects_dir / project)
        github_nwos = [nwo for u in all_urls if (nwo := parse_github_nwo(u)) is not None]

        if not github_nwos:
            if skip_reason:
                skipped.append((project, skip_reason))
            elif all_urls:
                hosts = list({re.sub(r"https?://([^/]+)/.*", r"\1", u) for u in all_urls})
                skipped.append((project, f"No GitHub repos (hosted on: {', '.join(hosts)})"))
            else:
                skipped.append((project, "No repositories found"))
            continue

        repo_names = [nwo.split("/")[1] for nwo in github_nwos]
        missing_main: list[str] = []
        forked_main: list[str] = []
        missing_submodules: list[str] = []

        for repo_name in repo_names:
            if repo_exists(args.org, repo_name):
                forked_main.append(repo_name)
                for sub in get_submodule_repo_names(args.org, repo_name):
                    if not repo_exists(args.org, sub):
                        missing_submodules.append(f"{repo_name}/{sub}")
            else:
                missing_main.append(repo_name)

        if not missing_main and not missing_submodules:
            ok.append(project)
        elif missing_main and not forked_main:
            not_forked.append((project, f"Not forked: {', '.join(missing_main)}"))
        else:
            details = []
            if missing_main:
                details.append(f"Missing repos: {', '.join(missing_main)}")
            if missing_submodules:
                details.append(f"Missing submodules: {', '.join(missing_submodules)}")
            partial.append((project, " | ".join(details)))

    print(" " * 60, end="\r")  # clear progress line

    for label, items in [
        ("NOT FORKED", not_forked),
        ("PARTIALLY FORKED", partial),
        ("SKIPPED — no GitHub repos", skipped),
    ]:
        if not items:
            continue
        print(f"\n{'='*60}")
        print(f"{label} ({len(items)})")
        print("=" * 60)
        for project, detail in items:
            print(f"  {project:<40} {detail}")

    if args.show_ok:
        print(f"\n{'='*60}")
        print(f"FULLY FORKED ({len(ok)})")
        print("=" * 60)
        for project in ok:
            print(f"  {project}")

    print(f"\n{'='*60}")
    print("Summary")
    print("=" * 60)
    print(f"  Total projects:      {total}")
    print(f"  Fully forked:        {len(ok)}")
    print(f"  Partially forked:    {len(partial)}")
    print(f"  Not forked:          {len(not_forked)}")
    print(f"  Skipped (no GitHub): {len(skipped)}")

    if not_forked or partial:
        sys.exit(1)


if __name__ == "__main__":
    main()

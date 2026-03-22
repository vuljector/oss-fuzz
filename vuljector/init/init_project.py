#!/usr/bin/env python3
"""
Prepare a vuljector-project from an OSS-Fuzz project.

Creates <name>/ and setup/ at the vuljector-projects repo root (no projects/ folder),
copies project files from oss-fuzz, and writes project.json.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from utils._github import extract_urls, parse_github_nwo


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    _INIT_DIR = Path(__file__).resolve().parent
    oss_fuzz_root = _INIT_DIR.parent.parent
    default_vuljector = oss_fuzz_root.parent / "vuljector-projects"

    parser = argparse.ArgumentParser(
        description="Prepare a vuljector-project from an OSS-Fuzz project (copy setup, write project.json)."
    )
    parser.add_argument("project", help="OSS-Fuzz project name (e.g. flask)")
    parser.add_argument(
        "--oss-fuzz-dir",
        type=Path,
        default=oss_fuzz_root,
        help="Path to oss-fuzz repo (default: parent of vuljector dir)",
    )
    parser.add_argument(
        "--vuljector-projects-dir",
        type=Path,
        default=default_vuljector,
        help="Path to vuljector-projects repo root (default: workspace sibling vuljector-projects)",
    )
    parser.add_argument(
        "--generate-tests",
        action="store_true",
        help="Run generate_test_script.py after init to create unit_tests/test.sh",
    )
    return parser.parse_args()


def _pick_newest_branch(branches: list[str | None]) -> str | None:
    """Return the newest branch from a list by extracting version numbers, ignoring None entries."""
    named = [b for b in branches if b is not None]
    if not named:
        return None
    if len(named) == 1:
        return named[0]
    def _version_key(b: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", b))
    return max(named, key=_version_key)


def get_default_branch_sha(org: str, repo_name: str, branch: str | None = None) -> str:
    """Return the commit SHA of *branch* (or the default branch if None) for org/repo_name, or \"\" on failure."""
    if branch is None:
        result = subprocess.run(
            ["gh", "api", f"repos/{org}/{repo_name}", "-q", ".default_branch"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        branch = result.stdout.strip()
    result = subprocess.run(
        ["gh", "api", f"repos/{org}/{repo_name}/commits/{branch}", "-q", ".sha"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    oss_project_dir = args.oss_fuzz_dir / "projects" / args.project
    out_project_dir = args.vuljector_projects_dir / args.project
    setup_dir = out_project_dir / "setup"

    if not oss_project_dir.is_dir():
        print(f"ERROR: OSS-Fuzz project directory not found: {oss_project_dir}", file=sys.stderr)
        sys.exit(1)

    entries, skip_reason = extract_urls(oss_project_dir)
    if not entries:
        print(f"ERROR: {skip_reason}", file=sys.stderr)
        sys.exit(1)

    main_repo = entries[0][0]
    nwo = parse_github_nwo(main_repo) if main_repo else None
    if not nwo:
        print("ERROR: No GitHub repository URL found.", file=sys.stderr)
        sys.exit(1)

    main_repo_org, main_repo_name = nwo.split("/", 1)

    # Pick the newest branch listed for the main repo across all clone commands
    main_repo_branches = [branch for url, branch in entries if url == main_repo]
    main_branch = _pick_newest_branch(main_repo_branches)

    setup_dir.mkdir(parents=True, exist_ok=True)

    for f in oss_project_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, setup_dir / f.name)

    secure_base_commit = ""
    if main_repo_name:
        secure_base_commit = get_default_branch_sha(main_repo_org, main_repo_name, main_branch)
        if not secure_base_commit:
            print("WARNING: Could not get branch commit (gh api); secure_base_commit left empty.", file=sys.stderr)

    target_dir = main_repo_name or args.project

    # ------------------------------------------------------------------
    # Submodule step: add the main_repo as a submodule under
    # <project>/codebase/<main_repo_name> for local code access.
    # The Dockerfile is kept unmodified so the image builds correctly
    # with source + deps baked in. At runtime the submodule is
    # bind-mounted over /src/<target_dir> to shadow the baked-in code.
    # ------------------------------------------------------------------
    dockerfile_path = setup_dir / "Dockerfile"
    if dockerfile_path.exists():
        submodule_rel_path = f"{args.project}/codebase/{main_repo_name}"
        cmd = ["git", "-C", str(args.vuljector_projects_dir),
               "submodule", "add"]
        if main_branch:
            cmd += ["--branch", main_branch]
        cmd += [main_repo, submodule_rel_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(
                f"WARNING: git submodule add failed for {main_repo}: "
                f"{result.stderr.strip()}",
                file=sys.stderr,
            )
        else:
            print(f"  submodule add: {submodule_rel_path} -> {main_repo}")

    project_json = {
        "schema_version": "v2",
        "project": args.project,
        "repo": {
            "url": main_repo or "",
            "branch": main_branch,
        },
        "target_dir": target_dir,
        "secure_base_commit": secure_base_commit,
        "unit_tests": {
            "enabled": False,
            "expected_passing_count": None,
        },
    }

    (out_project_dir / "project.json").write_text(
        json.dumps(project_json, indent=2) + "\n"
    )

    (out_project_dir / "vulnerabilities").mkdir(exist_ok=True)
    (out_project_dir / "debug" / "success").mkdir(parents=True, exist_ok=True)
    (out_project_dir / "debug" / "failed").mkdir(parents=True, exist_ok=True)

    print(f"Created {out_project_dir}")
    print(f"  setup/ with copied files")
    print(
        "  project.json ("
        f"repo={main_repo!r}, "
        f"target_dir={target_dir!r}, "
        f"secure_base_commit={secure_base_commit!r})"
    )
    print("  vulnerabilities/ and debug/(success|failed) (empty)")

    if args.generate_tests:
        generate_script = Path(__file__).resolve().parent / "generate_test_script.py"
        result = subprocess.run(
            [sys.executable, str(generate_script), args.project,
             "--vuljector-projects-dir", str(args.vuljector_projects_dir)],
        )
        if result.returncode != 0:
            print("WARNING: generate_test_script.py failed — unit_tests/ not created.", file=sys.stderr)


if __name__ == "__main__":
    main()

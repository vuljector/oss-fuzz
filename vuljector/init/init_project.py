#!/usr/bin/env python3
"""
Prepare a vuljector-project from an OSS-Fuzz project.

Creates <name>/ and setup/ at the vuljector-projects repo root (no projects/ folder),
copies project files from oss-fuzz, rewrites GitHub URLs to the target org (e.g. vuljector),
and writes project.json. Does not run git clone or fork; assumes repos are already forked.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from shared._github import extract_urls, normalise_url, parse_github_nwo


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    _INIT_DIR = Path(__file__).resolve().parent
    oss_fuzz_root = _INIT_DIR.parent.parent
    default_vuljector = oss_fuzz_root.parent / "vuljector-projects"

    parser = argparse.ArgumentParser(
        description="Prepare a vuljector-project from an OSS-Fuzz project (copy setup, rewrite URLs, write project.json)."
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
        "--org",
        default="vuljector",
        help="GitHub org for forked repos (default: vuljector)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing files",
    )
    return parser.parse_args()


def get_default_branch_sha(org: str, repo_name: str) -> str:
    """Return the current commit SHA of the default branch for org/repo_name, or \"\" on failure."""
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

    urls, skip_reason = extract_urls(oss_project_dir)
    if not urls:
        print(f"ERROR: {skip_reason}", file=sys.stderr)
        sys.exit(1)

    main_repo = urls[0]
    github_urls = [u for u in urls if parse_github_nwo(u) is not None]
    if not github_urls:
        print("ERROR: No GitHub repository URLs found.", file=sys.stderr)
        sys.exit(1)

    replace_map: dict[str, str] = {}
    for url in github_urls:
        nwo = parse_github_nwo(url)
        if nwo is None:
            continue
        repo_name = nwo.split("/")[1]
        fork_url = f"https://github.com/{args.org}/{repo_name}"
        replace_map[url] = fork_url
        if not url.endswith(".git"):
            replace_map[url + ".git"] = fork_url

    if args.dry_run:
        print(f"[dry-run] Would create {out_project_dir}")
        print(f"[dry-run] Would create {setup_dir}")
        print("[dry-run] Would copy from", oss_project_dir, "->", setup_dir)
        for old, new in replace_map.items():
            if not old.endswith(".git") or old not in replace_map:
                print(f"  Rewrite: {old} -> {new}")
        print(f"[dry-run] Would write project.json with original_main_repo={main_repo!r}, forked_main_repo=...")
        return

    setup_dir.mkdir(parents=True, exist_ok=True)

    for f in oss_project_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, setup_dir / f.name)

    for f in setup_dir.iterdir():
        if not f.is_file():
            continue
        try:
            text = f.read_text()
        except Exception as e:
            print(f"WARNING: Could not read {f}: {e}", file=sys.stderr)
            continue
        new_text = text
        for old_url, new_url in replace_map.items():
            new_text = new_text.replace(old_url, new_url)
        if new_text != text:
            f.write_text(new_text)

    if main_repo and parse_github_nwo(main_repo):
        main_repo_name = parse_github_nwo(main_repo).split("/")[1]
        forked_main_repo = f"https://github.com/{args.org}/{main_repo_name}"
    else:
        forked_main_repo = list(replace_map.values())[0] if replace_map else ""
        main_repo_name = ""

    secure_base_commit = ""
    if main_repo_name and not args.dry_run:
        secure_base_commit = get_default_branch_sha(args.org, main_repo_name)
        if not secure_base_commit:
            print("WARNING: Could not get default-branch commit for forked repo (gh api); secure_base_commit left empty.", file=sys.stderr)

    project_json = {
        "project": args.project,
        "source": {
            "oss_fuzz_project_dir": str(Path("oss-fuzz") / "projects" / args.project),
        },
        "repos": {
            "original_main_repo": main_repo or "",
            "forked_main_repo": forked_main_repo,
        },
        "secure_base_commit": secure_base_commit,
    }

    (out_project_dir / "project.json").write_text(
        json.dumps(project_json, indent=2) + "\n"
    )

    (out_project_dir / "vulnerabilities").mkdir(exist_ok=True)

    print(f"Created {out_project_dir}")
    print(f"  setup/ with copied and rewritten files")
    print(f"  project.json (original_main_repo={main_repo!r}, forked_main_repo={forked_main_repo!r}, secure_base_commit={secure_base_commit!r})")
    print(f"  vulnerabilities/ (empty)")


if __name__ == "__main__":
    main()

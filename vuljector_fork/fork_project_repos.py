#!/usr/bin/env python3
"""Fork all GitHub repositories for a given OSS-Fuzz project into a target org."""

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

from _github import extract_urls, normalise_url, parse_github_nwo, repo_exists

_DEFAULT_OSS_FUZZ_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fork OSS-Fuzz project repos (and submodules) into a GitHub org."
    )
    parser.add_argument("project", help="OSS-Fuzz project name (e.g. libpng)")
    parser.add_argument(
        "--oss-fuzz-dir",
        default=str(_DEFAULT_OSS_FUZZ_DIR),
        help="Path to local oss-fuzz clone (default: parent of this script's directory)",
    )
    parser.add_argument(
        "--org",
        default="vuljector",
        help="Target GitHub org to fork into",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without executing",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Forking
# ---------------------------------------------------------------------------

def fork_repo(nwo: str, org: str, dry_run: bool) -> str:
    """
    Fork *nwo* into *org*.

    Returns one of:
      - ``'forked'``        – fork was created
      - ``'already_exists'`` – repo already exists in org
      - ``'error:<msg>'``    – fork failed
    """
    repo_name = nwo.split("/")[1]
    if not dry_run and repo_exists(org, repo_name):
        return "already_exists"

    cmd = ["gh", "repo", "fork", nwo, "--org", org, "--clone=false"]
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return "forked"

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        time.sleep(10)  # respect GitHub's ~30 forks/hour rate limit
        return "forked"
    stderr = result.stderr.strip()
    if "already exists" in stderr.lower():
        return "already_exists"
    return f"error:{stderr}"


# ---------------------------------------------------------------------------
# Submodule handling
# ---------------------------------------------------------------------------

def _fetch_gitmodules(
    org: str, repo_name: str, fallback_nwo: str | None = None
) -> tuple[str, str] | None:
    """
    Fetch ``.gitmodules`` from the fork (falling back to *fallback_nwo* if the
    fork doesn't exist yet, e.g. during a dry run).

    Returns ``(content, sha)`` or ``None``.
    """
    candidates = [f"{org}/{repo_name}"]
    if fallback_nwo:
        candidates.append(fallback_nwo)

    for nwo in candidates:
        result = subprocess.run(
            ["gh", "api", f"repos/{nwo}/contents/.gitmodules"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout)
            content = base64.b64decode(data["content"]).decode()
            return content, data["sha"]
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            print(f"  WARNING: Could not parse .gitmodules for {nwo}: {exc}", file=sys.stderr)
            return None
    return None


def _parse_gitmodules_urls(content: str) -> list[str]:
    """Extract all ``url = …`` values from a .gitmodules file."""
    urls = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("url"):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                urls.append(parts[1].strip())
    return urls


def _commit_gitmodules(
    org: str, repo_name: str, content: str, sha: str, dry_run: bool
) -> bool:
    """Commit updated .gitmodules to the fork. Returns True on success."""
    if dry_run:
        print(f"  [dry-run] Would update .gitmodules in {org}/{repo_name}")
        return True

    body = json.dumps({
        "message": "chore: repoint submodules to vuljector forks",
        "content": base64.b64encode(content.encode()).decode(),
        "sha": sha,
    })
    result = subprocess.run(
        [
            "gh", "api", f"repos/{org}/{repo_name}/contents/.gitmodules",
            "-X", "PUT", "--input", "-",
        ],
        input=body,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            f"  WARNING: Failed to update .gitmodules in {org}/{repo_name}: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return True


def _process_submodules(
    org: str,
    repo_name: str,
    dry_run: bool,
    seen: set[str],
    upstream_nwo: str | None = None,
) -> None:
    """
    Recursively fork GitHub submodules and repoint ``.gitmodules`` in the fork.

    *seen* tracks ``"{org}/{repo}"`` keys to prevent cycles.
    *upstream_nwo* is used as a fallback when the fork doesn't exist yet.
    """
    result = _fetch_gitmodules(org, repo_name, fallback_nwo=upstream_nwo)
    if result is None:
        return

    content, sha = result
    urls = _parse_gitmodules_urls(content)
    if not urls:
        return

    url_map: dict[str, str] = {}

    for url in urls:
        norm = normalise_url(url)
        nwo = parse_github_nwo(norm)
        if nwo is None:
            print(f"  WARNING: Non-GitHub submodule skipped: {url}", file=sys.stderr)
            continue

        sub_repo = nwo.split("/")[1]
        key = f"{org}/{sub_repo}"
        new_url = f"https://github.com/{org}/{sub_repo}"

        if key not in seen:
            seen.add(key)
            print(f"  Submodule {nwo} ...")
            status = fork_repo(nwo, org, dry_run)
            if status == "forked":
                print(f"    Forked -> {org}/{sub_repo}")
            elif status == "already_exists":
                print(f"    Already exists in {org}, skipping.")
            else:
                print(
                    f"    ERROR forking {nwo}: {status.removeprefix('error:')}",
                    file=sys.stderr,
                )
            _process_submodules(org, sub_repo, dry_run, seen, upstream_nwo=nwo)

        if norm != new_url:
            url_map[url] = new_url
            if norm != url:  # also remap the .git-suffixed variant
                url_map[norm] = new_url

    if not url_map:
        return

    new_content = content
    for old, new in url_map.items():
        new_content = new_content.replace(old, new)

    if new_content != content:
        print(f"  Updating .gitmodules in {org}/{repo_name} ...")
        _commit_gitmodules(org, repo_name, new_content, sha, dry_run)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    project_dir = Path(args.oss_fuzz_dir) / "projects" / args.project

    if not project_dir.is_dir():
        print(f"ERROR: Project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    all_urls, skip_reason = extract_urls(project_dir)
    if not all_urls:
        print(f"Skipping {args.project}: {skip_reason}")
        return

    github_urls = [u for u in all_urls if parse_github_nwo(u) is not None]
    non_github = [u for u in all_urls if parse_github_nwo(u) is None]

    if non_github:
        print("WARNING: Non-GitHub URLs skipped:")
        for url in non_github:
            print(f"  {url}")
        print()

    if not github_urls:
        print("No GitHub repositories found for this project.")
        return

    forked: list[str] = []
    already_exists: list[str] = []
    errors: list[tuple[str, str]] = []
    submodule_seen: set[str] = set()

    for url in github_urls:
        nwo = parse_github_nwo(url)
        assert nwo is not None
        print(f"Processing {nwo} ...")
        status = fork_repo(nwo, args.org, args.dry_run)
        repo_name = nwo.split("/")[1]

        if status == "forked":
            print(f"  Forked -> {args.org}/{repo_name}")
            forked.append(nwo)
        elif status == "already_exists":
            print(f"  Already exists in {args.org}, skipping.")
            already_exists.append(nwo)
        else:
            msg = status.removeprefix("error:")
            print(f"  ERROR: {msg}", file=sys.stderr)
            errors.append((nwo, msg))
            continue

        submodule_seen.add(f"{args.org}/{repo_name}")
        _process_submodules(
            args.org, repo_name, args.dry_run, submodule_seen, upstream_nwo=nwo
        )

    print()
    print("=== Summary ===")
    print(f"  Forked:        {len(forked)}")
    print(f"  Already forked:{len(already_exists)}")
    print(f"  Errors:        {len(errors)}")
    if errors:
        for nwo, msg in errors:
            print(f"    {nwo}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()

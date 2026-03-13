#!/usr/bin/env python3
"""Fork all GitHub repositories for a given OSS-Fuzz project into a target org."""

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fork OSS-Fuzz project repos into a GitHub org."
    )
    parser.add_argument("project", help="OSS-Fuzz project name (e.g. libpng)")
    parser.add_argument(
        "--oss-fuzz-dir",
        default="/home/anl31/documents/code/vulinjector/oss-fuzz",
        help="Path to local oss-fuzz clone",
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


def extract_urls_from_yaml(project_yaml: Path) -> list[str]:
    if not project_yaml.exists():
        print(f"ERROR: {project_yaml} not found", file=sys.stderr)
        sys.exit(1)
    data = yaml.safe_load(project_yaml.read_text())
    main_repo = data.get("main_repo", "")
    return [main_repo] if main_repo else []


def extract_urls_from_dockerfile(dockerfile: Path) -> list[str]:
    if not dockerfile.exists():
        print(f"ERROR: {dockerfile} not found", file=sys.stderr)
        sys.exit(1)
    urls = []
    # Match git clone with any mix of long flags (--depth 1), short flags with
    # values (-b master), or bare short flags (-q), then capture the URL.
    pattern = re.compile(
        r"git clone\s+"
        r"(?:(?:--[\w=-]+|-\w(?:\s+\S+)?)\s+)*"
        r"(https?://\S+|git@\S+)",
        re.IGNORECASE,
    )
    for line in dockerfile.read_text().splitlines():
        for match in pattern.finditer(line):
            urls.append(match.group(1))
    return urls


def normalise_url(url: str) -> str:
    return url.removesuffix(".git")


def parse_github_nwo(url: str) -> str | None:
    """Return 'owner/repo' for a github.com URL, or None if not GitHub."""
    match = re.match(
        r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url, re.IGNORECASE
    )
    return match.group(1) if match else None


def repo_already_forked(nwo: str, org: str) -> bool:
    """Return True if org/repo already exists on GitHub."""
    repo_name = nwo.split("/")[1]
    result = subprocess.run(
        ["gh", "repo", "view", f"{org}/{repo_name}", "--json", "name"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def fork_repo(nwo: str, org: str, dry_run: bool) -> str:
    """Fork a repo. Returns 'forked', 'already_exists', or 'error:<msg>'."""
    if not dry_run and repo_already_forked(nwo, org):
        return "already_exists"

    cmd = ["gh", "repo", "fork", nwo, "--org", org, "--clone=false"]
    if dry_run:
        print(f"  [dry-run] {' '.join(cmd)}")
        return "forked"

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return "forked"
    stderr = result.stderr.strip()
    if "already exists" in stderr.lower():
        return "already_exists"
    return f"error:{stderr}"


def get_gitmodules(org: str, repo_name: str) -> tuple[str, str] | None:
    """Fetch .gitmodules from the fork. Returns (content_str, sha) or None."""
    result = subprocess.run(
        ["gh", "api", f"repos/{org}/{repo_name}/contents/.gitmodules"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        content = base64.b64decode(data["content"]).decode()
        sha = data["sha"]
        return content, sha
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"  WARNING: Failed to parse .gitmodules response: {e}", file=sys.stderr)
        return None


def parse_gitmodules_urls(content: str) -> list[str]:
    """Extract all url = <value> entries from a .gitmodules file."""
    urls = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("url"):
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                urls.append(parts[1].strip())
    return urls


def update_gitmodules(
    org: str, repo_name: str, new_content: str, sha: str, dry_run: bool
) -> bool:
    """Commit updated .gitmodules back to the fork. Returns True on success."""
    if dry_run:
        print(f"  [dry-run] Would commit updated .gitmodules to {org}/{repo_name}")
        return True

    encoded = base64.b64encode(new_content.encode()).decode()
    body = json.dumps(
        {
            "message": "chore: repoint submodules to vuljector forks",
            "content": encoded,
            "sha": sha,
        }
    )
    result = subprocess.run(
        [
            "gh", "api", f"repos/{org}/{repo_name}/contents/.gitmodules",
            "-X", "PUT",
            "--input", "-",
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


def process_submodules(
    org: str, repo_name: str, dry_run: bool, seen: set[str]
) -> None:
    """Fork all GitHub submodules of a repo and repoint .gitmodules to the forks."""
    result = get_gitmodules(org, repo_name)
    if result is None:
        return

    content, sha = result
    urls = parse_gitmodules_urls(content)
    if not urls:
        return

    url_map: dict[str, str] = {}

    for url in urls:
        norm = normalise_url(url)
        nwo = parse_github_nwo(norm)
        if nwo is None:
            print(f"  WARNING: Non-GitHub submodule URL skipped: {url}", file=sys.stderr)
            continue

        sub_repo = nwo.split("/")[1]
        key = f"{org}/{sub_repo}"

        if key not in seen:
            seen.add(key)
            print(f"  Submodule {nwo} ...")
            status = fork_repo(nwo, org, dry_run)
            if status == "forked":
                print(f"    Forked -> {org}/{sub_repo}")
            elif status == "already_exists":
                print(f"    Already exists in {org}, skipping.")
            else:
                msg = status.removeprefix("error:")
                print(f"    ERROR forking submodule {nwo}: {msg}", file=sys.stderr)
                # Still repoint URL even if fork failed (best-effort)

            # Recurse before recording URL map so nested submodules are handled
            process_submodules(org, sub_repo, dry_run, seen)

        new_url = f"https://github.com/{org}/{sub_repo}"
        if norm != new_url:
            url_map[url] = new_url
            if norm != url:  # also map the .git variant if present
                url_map[norm] = new_url

    if not url_map:
        return

    new_content = content
    for old, new in url_map.items():
        new_content = new_content.replace(old, new)

    if new_content != content:
        print(f"  Updating .gitmodules in {org}/{repo_name} ...")
        update_gitmodules(org, repo_name, new_content, sha, dry_run)


def main() -> None:
    args = parse_args()
    project_dir = Path(args.oss_fuzz_dir) / "projects" / args.project

    if not project_dir.is_dir():
        print(f"ERROR: project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    raw_urls = (
        extract_urls_from_yaml(project_dir / "project.yaml")
        + extract_urls_from_dockerfile(project_dir / "Dockerfile")
    )

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_urls: list[str] = []
    for url in raw_urls:
        norm = normalise_url(url)
        if norm not in seen:
            seen.add(norm)
            unique_urls.append(norm)

    github_urls: list[str] = []
    non_github_urls: list[str] = []
    for url in unique_urls:
        if parse_github_nwo(url) is not None:
            github_urls.append(url)
        else:
            non_github_urls.append(url)

    if non_github_urls:
        print("WARNING: The following URLs are not on GitHub and will be skipped:")
        for url in non_github_urls:
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
            print(f"  ERROR: {msg}")
            errors.append((nwo, msg))
            continue

        submodule_seen.add(f"{args.org}/{repo_name}")
        process_submodules(args.org, repo_name, args.dry_run, submodule_seen)

    print()
    print("=== Summary ===")
    print(f"  Forked:        {len(forked)}")
    print(f"  Already forked:{len(already_exists)}")
    print(f"  Errors:        {len(errors)}")
    if errors:
        for nwo, msg in errors:
            print(f"    {nwo}: {msg}")


if __name__ == "__main__":
    main()

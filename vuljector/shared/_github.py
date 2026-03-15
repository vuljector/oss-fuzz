"""Shared helpers: GitHub URL extraction from OSS-Fuzz project files and API calls."""

import re
import subprocess
from pathlib import Path

import yaml


def normalise_url(url: str) -> str:
    """Strip .git suffix and trailing slash."""
    return url.removesuffix(".git").rstrip("/")


def parse_github_nwo(url: str) -> str | None:
    """Return 'owner/repo' for a github.com URL, or None if not GitHub."""
    m = re.match(
        r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url, re.IGNORECASE
    )
    return m.group(1) if m else None


def extract_urls(project_dir: Path) -> tuple[list[str], str]:
    """
    Extract deduplicated normalised repository URLs from an OSS-Fuzz project.

    Parses ``project.yaml`` (``main_repo`` field) and ``Dockerfile``
    (``git clone`` commands).

    Returns ``(urls, skip_reason)``.  When the project cannot be parsed,
    *urls* is empty and *skip_reason* explains why.
    """
    project_yaml = project_dir / "project.yaml"
    dockerfile = project_dir / "Dockerfile"

    if not project_yaml.exists():
        return [], "Missing project.yaml"
    if not dockerfile.exists():
        return [], "Missing Dockerfile"

    raw: list[str] = []

    data = yaml.safe_load(project_yaml.read_text())
    main_repo = data.get("main_repo", "")
    if main_repo:
        raw.append(normalise_url(main_repo))

    clone_re = re.compile(
        r"git clone\s+(?:(?:--[\w=-]+|-\w(?:\s+\S+)?)\s+)*(https?://\S+|git@\S+)",
        re.IGNORECASE,
    )
    for line in dockerfile.read_text().splitlines():
        for m in clone_re.finditer(line):
            raw.append(normalise_url(m.group(1)))

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for url in raw:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    return unique, ""


def repo_exists(org: str, repo_name: str) -> bool:
    """Return True if *org*/*repo_name* exists on GitHub."""
    result = subprocess.run(
        ["gh", "api", f"repos/{org}/{repo_name}", "--silent"],
        capture_output=True,
    )
    return result.returncode == 0

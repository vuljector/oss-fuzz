#!/usr/bin/env python3
"""Fork all OSS-Fuzz project repositories into a target GitHub org."""

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_OSS_FUZZ_DIR = _SCRIPT_DIR.parent.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fork all OSS-Fuzz project repos into a GitHub org."
    )
    parser.add_argument(
        "--oss-fuzz-dir",
        default=str(_DEFAULT_OSS_FUZZ_DIR),
        help="Path to local oss-fuzz clone (default: parent of vuljector dir)",
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
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory for per-project logs (default: fork_logs/ next to this script)",
    )
    parser.add_argument(
        "projects",
        nargs="*",
        help="Specific project names to fork (default: all projects)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Per-project runner
# ---------------------------------------------------------------------------

def fork_project(
    project: str,
    oss_fuzz_dir: str,
    org: str,
    dry_run: bool,
    log_path: Path,
) -> tuple[str, bool]:
    """Run fork_project_repos.py for one project. Returns (project, success)."""
    cmd = [
        sys.executable,
        str(_SCRIPT_DIR / "fork_project_repos.py"),
        project,
        "--oss-fuzz-dir", oss_fuzz_dir,
        "--org", org,
    ]
    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout
    if result.stderr:
        output += "\n--- stderr ---\n" + result.stderr
    log_path.write_text(output)

    return project, result.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    oss_fuzz_dir = Path(args.oss_fuzz_dir)
    projects_dir = oss_fuzz_dir / "projects"

    if not projects_dir.is_dir():
        print(f"ERROR: Projects directory not found: {projects_dir}", file=sys.stderr)
        sys.exit(1)

    projects = args.projects or sorted(
        p.name for p in projects_dir.iterdir() if p.is_dir()
    )

    log_dir = Path(args.log_dir) if args.log_dir else _SCRIPT_DIR / "fork_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    total = len(projects)
    print(f"Forking {total} projects into {args.org} ...")
    if args.dry_run:
        print("(dry-run mode)")
    print()

    succeeded: list[str] = []
    failed: list[str] = []

    for i, project in enumerate(projects, 1):
        _, success = fork_project(
            project,
            str(oss_fuzz_dir),
            args.org,
            args.dry_run,
            log_dir / f"{project}.log",
        )
        status = "OK" if success else "FAIL"
        print(f"[{i}/{total}] {project}: {status}")
        (succeeded if success else failed).append(project)

    print()
    print("=== Summary ===")
    print(f"  Total:   {total}")
    print(f"  OK:      {len(succeeded)}")
    print(f"  Failed: {len(failed)}")
    if failed:
        print()
        print("Failed projects:")
        for p in failed:
            print(f"  {p}  (see {log_dir / p}.log)")
    print()
    print(f"Logs written to: {log_dir}")


if __name__ == "__main__":
    main()

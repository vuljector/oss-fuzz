#!/usr/bin/env python3
"""Batch-init projects from injection_pool.csv: init + Docker build + heuristic test detection.

Skips LLM fallback — only tries heuristic framework detection.
Produces a summary CSV at the end.

Usage:
    uv run python batch_init.py                  # init + build + detect + verify
    uv run python batch_init.py --no-verify      # init + build + detect (skip verify)
    uv run python batch_init.py --verify-only     # only verify projects with existing test.sh
"""
import argparse, csv, json, logging, shutil, subprocess, sys, time
from pathlib import Path

# Re-use internals from generate_test_script
from generate_test_script import (
    TEMPLATES_DIR, VERIFY_TIMEOUT,
    _codebase_mounts, _detect_framework, _make_test_sh,
)

log = logging.getLogger(__name__)

INIT_SCRIPT = Path(__file__).parent / "init_project.py"
VP_DIR = Path(__file__).resolve().parent.parent.parent.parent / "vuljector-projects"


def _verify_quick(image: str, unit_tests_dir: Path, mounts: list[str]) -> dict | None:
    """Run test.sh and extract JSON result — direct subprocess, no minisweagent."""
    cmd = (
        ["docker", "run", "--rm"]
        + ["-v", f"{unit_tests_dir}:/src/unit_tests",
           "-v", f"{unit_tests_dir / 'parse_results.py'}:/src/unit_tests/parse_results.py:ro"]
        + mounts
        + [image, "bash", "/src/unit_tests/test.sh"]
    )
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=VERIFY_TIMEOUT)
        for line in reversed(r.stdout.splitlines()):
            try:
                data = json.loads(line)
                if "passed" in data:
                    return data
            except json.JSONDecodeError:
                continue
    except subprocess.TimeoutExpired:
        log.warning("  verify timed out")
    except Exception as e:
        log.warning("  verify error: %s", e)
    return None


def process_project(name: str, *, no_verify: bool = False) -> dict:
    """Init + build + heuristic detect + optional verify for one project."""
    project_dir = VP_DIR / name
    project_json_path = project_dir / "project.json"
    unit_tests_dir = project_dir / "unit_tests"
    test_sh = unit_tests_dir / "test.sh"
    image_tag = f"vuljector-{name}"

    result = {"project": name, "status": "unknown", "framework": "", "passed": 0, "failed": 0, "error": ""}

    # Step 1: Init (skip if already done)
    if not project_json_path.is_file():
        r = subprocess.run(
            [sys.executable, str(INIT_SCRIPT), name, "--vuljector-projects-dir", str(VP_DIR)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            result["status"] = "init_failed"
            result["error"] = r.stderr.strip()[-200:]
            return result

    if not project_json_path.is_file():
        result["status"] = "init_failed"
        result["error"] = "project.json not created"
        return result

    project = json.loads(project_json_path.read_text())
    target_dir = project["target_dir"]
    mounts = _codebase_mounts(project, project_dir)

    # Step 2: Docker build
    setup_dir = project_dir / "setup"
    if not (setup_dir / "Dockerfile").exists():
        result["status"] = "no_dockerfile"
        return result

    r = subprocess.run(
        ["docker", "build", "-t", image_tag, "."],
        cwd=setup_dir, capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        result["status"] = "build_failed"
        result["error"] = r.stderr.strip()[-200:]
        return result

    # Step 3: Heuristic detection
    detected = _detect_framework(image_tag, target_dir, mounts)
    if not detected:
        result["status"] = "no_heuristic"
        return result

    result["framework"] = detected["name"]

    # Step 4: Write test.sh
    unit_tests_dir.mkdir(exist_ok=True)
    shutil.copy(TEMPLATES_DIR / "parse_results.py", unit_tests_dir / "parse_results.py")
    test_sh.write_text(_make_test_sh(target_dir, detected))
    test_sh.chmod(0o755)

    if no_verify:
        result["status"] = "detected"
        return result

    # Step 5: Verify
    verify = _verify_quick(image_tag, unit_tests_dir, mounts)
    if verify and verify.get("passed", 0) > 0:
        result["status"] = "ok"
        result["passed"] = verify["passed"]
        result["failed"] = verify.get("failed", 0)
        project["unit_tests"].update({"enabled": True, "expected_passing_count": verify["passed"]})
        project_json_path.write_text(json.dumps(project, indent=2) + "\n")
    elif verify:
        result["status"] = "zero_passed"
        result["passed"] = verify.get("passed", 0)
        result["failed"] = verify.get("failed", 0)
    else:
        result["status"] = "verify_failed"

    return result


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--no-verify", action="store_true", help="Skip test verification (just init + build + detect)")
    p.add_argument("--verify-only", action="store_true", help="Only verify projects that already have test.sh")
    p.add_argument("--csv", type=Path,
                   default=Path(__file__).resolve().parent.parent.parent.parent / "select-projects" / "injection_pool.csv")
    args = p.parse_args()

    if not args.csv.exists():
        sys.exit(f"ERROR: {args.csv} not found")

    with open(args.csv) as f:
        projects = [row["name"] for row in csv.DictReader(f)]

    # Skip already-completed projects (enabled=true in project.json)
    existing = set()
    for d in VP_DIR.iterdir():
        pj = d / "project.json"
        if pj.exists():
            data = json.loads(pj.read_text())
            if data.get("unit_tests", {}).get("enabled"):
                existing.add(d.name)

    if args.verify_only:
        # Only process projects that have test.sh but not yet enabled
        todo = []
        for name in projects:
            if name in existing:
                continue
            if (VP_DIR / name / "unit_tests" / "test.sh").exists():
                todo.append(name)
    else:
        todo = [p for p in projects if p not in existing]

    log.info("Total: %d, Already done: %d, To process: %d", len(projects), len(existing), len(todo))

    results = []
    for i, name in enumerate(todo):
        log.info("[%d/%d] %s", i + 1, len(todo), name)
        t0 = time.time()
        try:
            r = process_project(name, no_verify=args.no_verify)
        except Exception as e:
            r = {"project": name, "status": "exception", "framework": "", "passed": 0, "failed": 0, "error": str(e)[:200]}
        elapsed = time.time() - t0
        log.info("  -> %s (%s, %d passed) [%.0fs]", r["status"], r["framework"], r["passed"], elapsed)
        results.append(r)

    # Write summary
    out_path = Path(__file__).parent / "batch_results.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["project", "status", "framework", "passed", "failed", "error"])
        w.writeheader()
        w.writerows(results)

    # Print summary
    from collections import Counter
    counts = Counter(r["status"] for r in results)
    log.info("\n=== Summary ===")
    for status, count in counts.most_common():
        log.info("  %-20s %d", status, count)
    log.info("Results written to %s", out_path)


if __name__ == "__main__":
    main()

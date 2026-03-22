#!/usr/bin/env python3
"""Generate vuljector-projects/<project>/unit_tests/test.sh."""
import argparse, json, logging, os, shutil, subprocess, sys
from pathlib import Path

import yaml
from dotenv import dotenv_values
from minisweagent import package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.docker import DockerEnvironment
from minisweagent.models import get_model

TEMPLATES_DIR = Path(__file__).parent / "test_templates"
VERIFY_TIMEOUT = 600
log = logging.getLogger(__name__)

# Lines prepended to every generated test.sh that compiles native code.
_SANITIZER_CLEAR = """\
# Clear OSS-Fuzz sanitizer/fuzzer flags that break normal builds
unset SANITIZER_FLAGS LIB_FUZZING_ENGINE
export CFLAGS="" CXXFLAGS="" LDFLAGS="" RUSTFLAGS=""\
"""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("project")
    p.add_argument("--vuljector-projects-dir", type=Path,
                   default=Path(__file__).resolve().parent.parent.parent.parent / "vuljector-projects")
    p.add_argument("--force", action="store_true", help="Overwrite existing test.sh")
    return p.parse_args()


def _codebase_mounts(project: dict, project_dir: Path) -> list[str]:
    repo_url = project.get("repo", {}).get("url", "")
    if not repo_url:
        return []
    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    target_dir = project.get("target_dir", repo_name)
    host = project_dir / "codebase" / repo_name
    return ["-v", f"{host}:/src/{target_dir}"]


def _detect_framework(image: str, target_dir: str, mounts: list[str] | None = None) -> dict | None:
    """Check heuristic rules against the image; return the first matching rule dict."""
    for h in yaml.safe_load((TEMPLATES_DIR / "heuristics.yaml").read_text()):
        check = " || ".join(f'[ -f /src/{target_dir}/{s} ]' for s in h["signals"])
        r = subprocess.run(
            ["docker", "run", "--rm"] + (mounts or []) + [image, "bash", "-c", f'if {check}; then echo yes; fi'],
            capture_output=True, text=True, timeout=30,
        )
        if r.stdout.strip() == "yes":
            return h
    return None


def _make_test_sh(target_dir: str, rule: dict) -> str:
    """Build test.sh content from a matched heuristic rule."""
    setup = rule.get("setup", "")
    cmd = rule["cmd"].replace("{target_dir}", target_dir)
    framework = rule["framework"]
    needs_clear = rule.get("needs_sanitizer_clear", False)

    lines = ["#!/bin/bash", f"cd /src/{target_dir}"]
    if needs_clear:
        lines.append(_SANITIZER_CLEAR)
    if setup:
        lines.append(setup)
    lines.append(f"{cmd} 2>&1 | python3 /src/unit_tests/parse_results.py --framework {framework}")
    return "\n".join(lines) + "\n"


def _unit_tests_mounts(unit_tests_dir: Path) -> list[str]:
    """Mount unit_tests/ with parse_results.py read-only so the agent cannot overwrite it."""
    return [
        "-v", f"{unit_tests_dir}:/src/unit_tests",
        "-v", f"{unit_tests_dir / 'parse_results.py'}:/src/unit_tests/parse_results.py:ro",
    ]


def _verify(image: str, unit_tests_dir: Path, mounts: list[str] | None = None) -> dict | None:
    env = DockerEnvironment(
        image=image,
        run_args=["--rm"] + _unit_tests_mounts(unit_tests_dir) + (mounts or []),
        timeout=VERIFY_TIMEOUT,
    )
    try:
        result = env.execute({"command": "bash /src/unit_tests/test.sh"}, timeout=VERIFY_TIMEOUT)
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            try:
                data = json.loads(line)
                if "passed" in data:
                    return data
            except json.JSONDecodeError:
                continue
        # Log tail of output when no JSON found for debugging
        log.warning("No JSON summary found in test output. Last 20 lines:\n%s",
                    "\n".join(output.splitlines()[-20:]))
    except Exception as e:
        log.warning("Verification error: %s", e)
    finally:
        try: env.cleanup()
        except Exception: pass
    return None


_LLM_TASK = """\
You are inside an OSS-Fuzz Docker container.  The project source is at /src/{target_dir}.

## Goal
Write /src/unit_tests/test.sh — a bash script that builds and runs the project's
existing unit / integration tests, producing a JSON summary as its last line.

## Critical: OSS-Fuzz environment quirks
These Docker images are built for fuzzing, NOT for normal development.  You MUST
work around these issues:

1. **Sanitizer flags** — The environment sets SANITIZER_FLAGS, LIB_FUZZING_ENGINE,
   CFLAGS, CXXFLAGS, LDFLAGS for fuzzer builds.  For normal compilation, clear them:
     unset SANITIZER_FLAGS LIB_FUZZING_ENGINE
     export CFLAGS="" CXXFLAGS="" LDFLAGS="" RUSTFLAGS=""

2. **No git** — The source at /src/{target_dir} may have a broken .git (submodule
   pointer to host).  If git is needed for the build, either set GIT_DIR=/dev/null
   or copy the source to /tmp and git-init a fresh repo.

3. **Do NOT use build.sh or the fuzzing harness** — those require fuzzing
   infrastructure that is not present.  Build and test using the project's native
   build system (make, cmake, cargo, go, pip, mvn, gradle, npm, etc.).

4. **Missing dependencies** — Some test deps may not be pre-installed.  Use
   apt-get install, pip3 install, npm install, etc. as needed.

## Steps
1. Explore /src/{target_dir} to identify language, build system, and test suite.
2. Build the project with its native build system (clearing sanitizer flags first
   if it compiles native code).
3. Install test dependencies, then run the existing tests.
4. Write /src/unit_tests/test.sh.  The LAST line MUST pipe test output through:
     <test_command> 2>&1 | python3 /src/unit_tests/parse_results.py --framework <FRAMEWORK>
   Supported frameworks: pytest, cargo, gotest, ctest, maven, gradle, jest, tap,
   phptest, btest, gtest, meson, unittest, generic.
   Do NOT modify parse_results.py — it is read-only.
5. Run `bash /src/unit_tests/test.sh` and confirm the last line is valid JSON
   with "passed" > 0.
6. When done: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
"""


def _llm_fallback(image: str, unit_tests_dir: Path, target_dir: str, mounts: list[str] | None = None) -> bool:
    agent_cfg = yaml.safe_load((package_dir / "config" / "default.yaml").read_text())["agent"]
    agent_cfg["step_limit"] = 40

    model = get_model(config={
        "model_name": "azure/GPT-5-nano",
        "model_class": "litellm_textbased",
        "cost_tracking": "ignore_errors",
        "model_kwargs": {"drop_params": True},
    })

    task = _LLM_TASK.format(target_dir=target_dir)

    env = DockerEnvironment(
        image=image, cwd=f"/src/{target_dir}",
        run_args=["--rm"] + _unit_tests_mounts(unit_tests_dir) + (mounts or []),
        timeout=VERIFY_TIMEOUT,
    )
    agent = DefaultAgent(model, env, **agent_cfg)
    try:
        agent.run(task=task)
    finally:
        try: env.cleanup()
        except Exception: pass

    return (unit_tests_dir / "test.sh").exists()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()

    project_dir = args.vuljector_projects_dir / args.project
    project_json_path = project_dir / "project.json"
    unit_tests_dir = project_dir / "unit_tests"
    test_sh = unit_tests_dir / "test.sh"

    if not project_json_path.is_file():
        sys.exit(f"ERROR: {project_json_path} not found — run init_project.py first.")
    if test_sh.is_file() and not args.force:
        sys.exit(f"ERROR: {test_sh} already exists. Use --force to overwrite.")

    project = json.loads(project_json_path.read_text())
    target_dir = project["target_dir"]
    mounts = _codebase_mounts(project, project_dir)
    image_tag = f"vuljector-{args.project}"
    dotenv = dotenv_values(Path(__file__).parent / ".env")
    os.environ.update({
        "AZURE_API_KEY": dotenv.get("AZURE_API_KEY", ""),
        "AZURE_API_BASE": dotenv.get("AZURE_ENDPOINT", ""),
        "AZURE_API_VERSION": dotenv.get("AZURE_API_VERSION", ""),
    })

    log.info("Building %s ...", image_tag)
    subprocess.run(["docker", "build", "-t", image_tag, "."], cwd=project_dir / "setup", check=True)

    unit_tests_dir.mkdir(exist_ok=True)
    shutil.copy(TEMPLATES_DIR / "parse_results.py", unit_tests_dir / "parse_results.py")

    detected = _detect_framework(image_tag, target_dir, mounts)
    if detected:
        log.info("Detected framework: %s (%s)", detected["framework"], detected["name"])
        test_sh.write_text(_make_test_sh(target_dir, detected))
        test_sh.chmod(0o755)
        result = _verify(image_tag, unit_tests_dir, mounts)
        if result and result["passed"] > 0:
            project["unit_tests"].update({"enabled": True, "expected_passing_count": result["passed"]})
            project_json_path.write_text(json.dumps(project, indent=2) + "\n")
            log.info("Done: %d passing tests", result["passed"])
            return
        log.info("Heuristic test.sh yielded no passing tests — trying LLM fallback.")
    else:
        log.info("No framework detected — trying LLM fallback.")

    log.info("Running mini-swe-agent fallback...")
    if not _llm_fallback(image_tag, unit_tests_dir, target_dir, mounts):
        sys.exit("ERROR: LLM agent did not produce test.sh.")

    result = _verify(image_tag, unit_tests_dir, mounts)
    if result and result["passed"] > 0:
        project["unit_tests"].update({"enabled": True, "expected_passing_count": result["passed"]})
        project_json_path.write_text(json.dumps(project, indent=2) + "\n")
        log.info("Done: %d passing tests", result["passed"])
    else:
        sys.exit("ERROR: Could not find passing unit tests for this project.")


if __name__ == "__main__":
    main()

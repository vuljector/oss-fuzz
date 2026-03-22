# init

Initialize vuljector-projects from OSS-Fuzz projects and generate unit test scripts.

## Setup

```bash
cd vuljector/init
uv sync
cp .env.example .env   # fill in Azure API credentials for LLM fallback
```

## Scripts

### `init_project.py`

Creates a vuljector-project directory: copies OSS-Fuzz setup files, adds the
upstream repo as a git submodule, and writes `project.json` (v2 schema).

```bash
uv run python init_project.py <project>
uv run python init_project.py flask --generate-tests
```

Options:
- `--oss-fuzz-dir` — path to oss-fuzz repo (default: auto-detected)
- `--vuljector-projects-dir` — path to vuljector-projects repo (default: workspace sibling)
- `--generate-tests` — also run `generate_test_script.py` after init

### `generate_test_script.py`

Generates `unit_tests/test.sh` for a project using a two-tier approach:

1. **Heuristic detection** — checks `test_templates/heuristics.yaml` rules against
   the Docker image to identify the build system and test framework
2. **LLM fallback** — if heuristics fail, spawns a mini-swe-agent (GPT-5-nano)
   inside the Docker container to explore and write the test script

```bash
uv run python generate_test_script.py <project>
uv run python generate_test_script.py flask --force
```

Options:
- `--vuljector-projects-dir` — path to vuljector-projects repo
- `--force` — overwrite existing `test.sh`

## Test templates

- **`test_templates/heuristics.yaml`** — ordered detection rules mapping file
  signals (e.g. `Cargo.toml`, `CMakeLists.txt`) to test commands and frameworks.
  Rules for native code set `needs_sanitizer_clear: true` to clear OSS-Fuzz
  sanitizer flags before building.

- **`test_templates/parse_results.py`** — universal test output normalizer.
  Reads test output from stdin, passes it through, and appends a JSON summary:
  `{"passed": N, "failed": M}`. Copied into each project's `unit_tests/` directory.
  Supported frameworks: `pytest`, `cargo`, `gotest`, `ctest`, `maven`, `gradle`,
  `jest`, `tap`, `phptest`, `btest`, `gtest`, `meson`, `unittest`, `generic`.

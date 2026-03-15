# Vuljector

Tools for preparing [vuljector-projects](https://github.com/vuljector/vuljector-projects) from OSS-Fuzz: fork upstream repos into an org and initialize project layout with rewritten URLs.

## Layout

| Directory | Purpose |
|-----------|--------|
| [**fork/**](fork/) | Fork OSS-Fuzz project repos (and submodules) into a GitHub org. Requires `gh` CLI and auth. |
| [**init/**](init/) | Initialize a vuljector-project: copy setup from oss-fuzz, rewrite GitHub URLs, write `project.json`. No forking. |
| **shared/** | Shared helpers (URL extraction, GitHub API). Used by both fork and init. |

## Typical workflow

1. **Fork** upstream repos for an OSS-Fuzz project into your org (e.g. `vuljector`):
   ```bash
   cd fork
   python3 fork_project_repos.py <project>   # one project
   # or
   python3 fork_all_projects.py              # all projects (batch)
   ```

2. **Verify** forks exist (optional):
   ```bash
   cd fork
   python3 verify_forks.py [--show-ok]
   ```

3. **Initialize** the vuljector-project (copies setup, rewrites URLs, writes `project.json`):
   ```bash
   cd init
   python3 init_project.py <project> [--vuljector-projects-dir /path/to/vuljector-projects]
   ```

All scripts accept `--oss-fuzz-dir` to point at your oss-fuzz clone (default: parent of this `vuljector` directory).

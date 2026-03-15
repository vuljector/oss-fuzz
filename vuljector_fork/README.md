# vuljector_fork

Scripts to fork all OSS-Fuzz project repositories (and their git submodules) into
the `vuljector` GitHub organisation, making each fork fully self-contained.

## Prerequisites

- [GitHub CLI (`gh`)](https://cli.github.com/) — authenticated with write access to the target org
- Python 3.10+, `pyyaml` (`pip install pyyaml`)

## Scripts

| Script | Purpose |
|--------|---------|
| `fork_project_repos.py` | Fork a single OSS-Fuzz project's repos + submodules |
| `fork_all_projects.py`  | Batch runner — iterate all (or specified) projects |
| `verify_forks.py`       | Check GitHub API directly to confirm forks exist |

`_github.py` is a shared utility module (not a standalone script).

## Usage

### Fork a single project

```bash
python3 fork_project_repos.py libpng
python3 fork_project_repos.py libpng --dry-run   # preview without executing
```

### Fork all projects

```bash
python3 fork_all_projects.py                     # all projects, logs → fork_logs/
python3 fork_all_projects.py zydis boost         # specific projects only
python3 fork_all_projects.py --dry-run
```

### Verify forks

```bash
python3 verify_forks.py                          # report missing / partial forks
python3 verify_forks.py --show-ok                # also list fully forked projects
```

## Common options

All scripts accept:

| Flag | Default | Description |
|------|---------|-------------|
| `--oss-fuzz-dir PATH` | parent of this directory | Path to local oss-fuzz clone |
| `--org ORG` | `vuljector` | Target GitHub organisation |
| `--dry-run` | — | Print actions without executing |

`fork_all_projects.py` additionally accepts `--log-dir PATH`
(default: `fork_logs/` next to this script).

## Rate limits

GitHub limits fork creation to ~30 per hour per account.
`fork_project_repos.py` sleeps 10 seconds after each real fork.
When rate-limited, re-run `fork_all_projects.py` passing only the failed projects.

## Known permanent failures

Some projects cannot be forked automatically:

- **Private / deleted** upstream repos (e.g. `bignum-fuzzer`, `piex`)
- **GitHub wiki** repos (`.wiki` URLs — not forkable via the API)
- **Non-GitHub** hosts (GitLab, SourceForge, Chromium, etc.)

These will appear as "skipped" or "not forked" in `verify_forks.py` output and
can be ignored.

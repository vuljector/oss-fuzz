# Fork

Fork OSS-Fuzz project repositories (and submodules) into a GitHub org. Requires the GitHub CLI (`gh`) and login.

## Installation

### GitHub CLI `gh` install

- **[macOS](https://github.com/cli/cli/blob/trunk/docs/install_macos.md)** — [Homebrew](https://github.com/cli/cli/blob/trunk/docs/install_macos.md#homebrew), [precompiled binaries](https://github.com/cli/cli/releases)
- **[Linux & Unix](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)** — [Debian/Ubuntu](https://github.com/cli/cli/blob/trunk/docs/install_linux.md#debian), [RPM](https://github.com/cli/cli/blob/trunk/docs/install_linux.md#rpm), [releases](https://github.com/cli/cli/releases)
- **[Windows](https://github.com/cli/cli/blob/trunk/docs/install_windows.md)** — [WinGet](https://github.com/cli/cli/blob/trunk/docs/install_windows.md#winget), [releases](https://github.com/cli/cli/releases)

Build from source: [GitHub CLI source](https://github.com/cli/cli/blob/trunk/docs/install_source.md).

### Login

```bash
gh auth login
```

## Scripts

- **fork_project_repos.py** — Fork all repos for one OSS-Fuzz project (and submodules).
- **fork_all_projects.py** — Run fork_project_repos for all (or selected) projects; logs per project.
- **verify_forks.py** — Check which projects are fully/partially forked in the target org.

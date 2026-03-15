# Init

Initialize a vuljector-project from an OSS-Fuzz project: copy setup files into the vuljector-projects repo, rewrite GitHub URLs to the fork org, and write `project.json`. Does not fork repos (run the [fork](../fork/) scripts first if needed).

## Usage

```bash
python3 init_project.py <project> [--vuljector-projects-dir /path/to/vuljector-projects] [--org vuljector]
```

Defaults: `--oss-fuzz-dir` is the parent of the vuljector dir (this oss-fuzz clone); `--vuljector-projects-dir` is the workspace sibling `vuljector-projects`.

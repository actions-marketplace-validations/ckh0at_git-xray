<div align="center">

# git-xray 🩻

**X-ray any git repository in one command.**
Find the code that hurts: hotspots, bus factor, hidden coupling, stale and orphaned files.

[![PyPI](https://img.shields.io/pypi/v/git-xray.svg)](https://pypi.org/project/git-xray/)
[![Python](https://img.shields.io/pypi/pyversions/git-xray.svg)](https://pypi.org/project/git-xray/)
[![CI](https://github.com/ckh0at/git-xray/actions/workflows/ci.yml/badge.svg)](https://github.com/ckh0at/git-xray/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

<img src="docs/demo.svg" alt="git-xray running on the Flask repository" width="820">

</div>

```bash
pipx install git-xray     # or: pip install git-xray / uvx git-xray
cd your-repo
git xray
```

No config, no server, no dependencies. Pure Python and `git`. It analyses Flask's
3,800 commits in **under a second**.

---

## Why?

Your git history already knows where the problems are. The files that change
all the time *and* are large are where bugs pile up and velocity goes to die.
Directories that only one person understands are a risk. Files that always change
together hint at a missing abstraction.

Tools like [code-maat](https://github.com/adamtornhill/code-maat) pioneered this
analysis (see *Your Code as a Crime Scene*), but they need a JVM, a log export
step and a spreadsheet. `git-xray` gives you the answers in one command.

## What it shows

| Section | Question it answers |
|---|---|
| 🔥 **Hotspots** | Which files change most often *and* are big? Your best refactoring targets. |
| 🚌 **Bus factor** | How many people would have to leave before nobody knows a directory? |
| 🔗 **Hidden coupling** | Which files keep changing in the same commits, even though nothing says they're related? |
| 🧊 **Stale code** | Which big files has nobody touched in a year? |
| 👻 **Orphaned code** | Which files were mostly written by someone who is no longer active? |
| 👥 **Contributors** | Who did the work in this time range? |

## Usage

```bash
git xray                          # whole history of the current repo
git xray --since "6 months ago"   # recent activity only
git xray src/ lib/                # limit to some paths
git xray -n 25                    # show 25 rows per section
git xray --depth 2                # bus factor per src/foo instead of src/
git xray -x "*.generated.ts"      # extra excludes (repeatable)
git xray -C ~/code/other-repo     # analyse another repo
```

### Output formats

```bash
git xray --md > XRAY.md           # Markdown: paste into a PR, wiki or issue
git xray --json | jq '.hotspots'  # JSON: build your own dashboards or CI checks
```

<details>
<summary>All options</summary>

```text
usage: git-xray [-h] [-C DIR] [--since WHEN] [-n TOP] [--depth DEPTH]
                [--min-coupling N] [--stale-days N] [-x GLOB]
                [--no-default-excludes] [--include-bots] [--json | --md]
                [--color {auto,always,never}] [-V]
                [paths ...]
```

| Option | Default | Meaning |
|---|---|---|
| `--since WHEN` | all history | Only commits newer than `WHEN` (anything `git log --since` accepts) |
| `-n, --top` | 10 | Rows per section |
| `--depth` | 1 | Directory depth used for bus-factor grouping |
| `--min-coupling` | 5 | Minimum shared commits before a pair is reported |
| `--stale-days` | 365 | Days without change before a file counts as stale / an author as inactive |
| `-x, --exclude` | | Glob to skip, repeatable |
| `--no-default-excludes` | | Keep lockfiles, `vendor/`, `dist/`, changelogs, minified files… |
| `--include-bots` | | Count dependabot, renovate and other bots |
| `--color` | auto | Respects `NO_COLOR` and `FORCE_COLOR` |

</details>

## How the numbers are computed

- **Hotspot score** = `commits / max_commits × log(loc) / log(max_loc)`. Change frequency
  matters most; size is log-scaled so one giant file does not drown out everything else.
- **Bus factor** = the smallest number of authors who together wrote more than 50% of the
  changed lines in a directory.
- **Coupling degree** = `shared_commits / average(commits of A, commits of B)`. Commits
  touching more than 30 files (mass renames, formatting) are ignored.
- **Orphaned** = the main author wrote at least 50% of the file and has made no commit in
  `--stale-days`.
- Renames are followed (`git log -M`), so history survives `git mv`. Merge commits are skipped.
  Author identities respect your `.mailmap`.

These are heuristics, not verdicts. A hotspot can be a healthy, central module; the
point is to know *where to look*.

## FAQ

**Is it fast on big repos?** It runs `git log` once and parses it in a single pass.
Most repos take a second or two. On huge monorepos, narrow it down with `--since` or a path.

**Does it send my code anywhere?** No. It runs `git` locally and prints to your terminal.

**Why `git xray` and not `git-xray`?** Both work. git treats any `git-<name>`
executable on your `PATH` as a subcommand.

## Contributing

Issues and PRs are welcome! Ideas we would love help with:
an HTML report, `--fail-on` thresholds for CI, and per-author knowledge maps.

```bash
git clone https://github.com/ckh0at/git-xray && cd git-xray
python -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest
```

## License

MIT

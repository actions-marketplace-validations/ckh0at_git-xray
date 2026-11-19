"""Mine `git log` into hotspots, ownership, coupling and staleness metrics.

Pure Python, zero third-party dependencies. The only external process is `git`.
"""

from __future__ import annotations

import fnmatch
import math
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations

# Commits touching more files than this are usually mass reformatting,
# vendoring or license-header sweeps; they only add noise to coupling.
MAX_FILES_FOR_COUPLING = 30

DEFAULT_EXCLUDES = [
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock", "uv.lock",
    "*.min.js", "*.min.css", "*.map", "*.svg", "*.snap",
    "vendor/*", "node_modules/*", "dist/*", "build/*", "third_party/*",
    "CHANGELOG*", "CHANGES*", "HISTORY*", "NEWS*", "RELEASE_NOTES*",
]

BOT_MARKERS = ("[bot]", "dependabot", "renovate", "github-actions", "pre-commit-ci")


def is_bot(author: str) -> bool:
    a = author.lower()
    return any(m in a for m in BOT_MARKERS)

COMMIT_MARK = "\x1e"
FIELD_SEP = "\x1f"


class GitError(RuntimeError):
    pass


def run_git(args: list[str], cwd: str) -> str:
    if not os.path.isdir(cwd):
        raise GitError(f"no such directory: {cwd}")
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError as exc:  # pragma: no cover
        raise GitError("git executable not found on PATH") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip() or "git failed")
    return proc.stdout.decode("utf-8", "replace")


@dataclass
class Commit:
    sha: str
    author: str
    timestamp: int
    changes: list[tuple[str, int, int]]  # (path, added, deleted)


@dataclass
class FileStats:
    path: str
    commits: int = 0
    added: int = 0
    deleted: int = 0
    first_seen: int = 0
    last_seen: int = 0
    authors: Counter = field(default_factory=Counter)  # author -> lines changed
    loc: int = 0

    @property
    def churn(self) -> int:
        return self.added + self.deleted

    @property
    def main_author(self) -> tuple[str, float]:
        if not self.authors:
            return ("?", 0.0)
        total = sum(self.authors.values()) or 1
        name, lines = self.authors.most_common(1)[0]
        return name, lines / total


_BRACE_RENAME = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")


def parse_numstat_path(raw: str) -> tuple[str | None, str]:
    """Return (old_path, new_path) from a numstat path column.

    git prints renames as ``old => new`` or ``dir/{old => new}/rest``.
    """
    m = _BRACE_RENAME.match(raw)
    if m:
        pre, old, new, post = m.groups()
        old_p = re.sub("/+", "/", pre + old + post)
        new_p = re.sub("/+", "/", pre + new + post)
        return old_p, new_p
    if " => " in raw:
        old, new = raw.split(" => ", 1)
        return old, new
    return None, raw


def read_log(repo: str, since: str | None = None, paths: list[str] | None = None,
             rev: str = "HEAD") -> list[Commit]:
    """Read history newest-first, normalising renamed paths to their latest name."""
    fmt = f"{COMMIT_MARK}%H{FIELD_SEP}%aN{FIELD_SEP}%at"
    args = ["-c", "core.quotepath=off", "log", rev, "--no-merges", "--numstat", "-M",
            f"--format={fmt}"]
    if since:
        args.append(f"--since={since}")
    if paths:
        args += ["--", *paths]
    out = run_git(args, repo)

    commits: list[Commit] = []
    renames: dict[str, str] = {}  # historical path -> current path

    def resolve(p: str) -> str:
        seen = set()
        while p in renames and p not in seen:
            seen.add(p)
            p = renames[p]
        return p

    for block in out.split(COMMIT_MARK):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        try:
            sha, author, ts = lines[0].split(FIELD_SEP)
        except ValueError:
            continue
        changes = []
        for line in lines[1:]:
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            a, d, raw = parts
            old, new = parse_numstat_path(raw)
            current = resolve(new)
            if old and old != new:
                # Newest-first: older commits mentioning `old` are this same file.
                renames.setdefault(old, current)
            added = int(a) if a.isdigit() else 0  # "-" for binary files
            deleted = int(d) if d.isdigit() else 0
            changes.append((current, added, deleted))
        commits.append(Commit(sha, author, int(ts), changes))
    return commits


def tracked_files(repo: str, rev: str = "HEAD") -> set[str]:
    out = run_git(["ls-tree", "-r", "--name-only", "-z", rev], repo)
    return {p for p in out.split("\0") if p}


def count_loc(repo_root: str, path: str) -> int:
    try:
        with open(os.path.join(repo_root, path), "rb") as fh:
            data = fh.read(4_000_000)
    except OSError:
        return 0
    if b"\0" in data[:8192]:
        return 0  # binary
    return sum(1 for line in data.splitlines() if line.strip())


def is_excluded(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/*"):
            d = pat[:-1]
            if path.startswith(d) or f"/{d}" in f"/{path}":
                return True
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(os.path.basename(path), pat):
            return True
    return False


def bus_factor(author_lines: Counter, threshold: float = 0.5) -> int:
    """Smallest number of authors who together wrote more than `threshold` of the changes."""
    total = sum(author_lines.values())
    if total == 0:
        return 0
    acc = n = 0
    for _, lines in author_lines.most_common():
        acc += lines
        n += 1
        if acc / total > threshold:
            break
    return n


def directory_of(path: str, depth: int) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) if parts else "."


@dataclass
class Report:
    repo: str
    commits: int
    authors: int
    first_ts: int
    last_ts: int
    files: int
    hotspots: list[dict]
    ownership: list[dict]
    coupling: list[dict]
    stale: list[dict]
    orphaned: list[dict]
    contributors: list[dict]

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "summary": {
                "commits": self.commits, "authors": self.authors, "files": self.files,
                "first_commit": self.first_ts, "last_commit": self.last_ts,
            },
            "hotspots": self.hotspots,
            "ownership": self.ownership,
            "coupling": self.coupling,
            "stale": self.stale,
            "orphaned": self.orphaned,
            "contributors": self.contributors,
        }


@dataclass
class History:
    """Everything mined from `git log`, before it is cut down into a Report."""
    root: str
    commits: list[Commit]
    files: dict[str, FileStats]
    pair_counts: Counter
    author_last: dict[str, int]
    contributor_commits: Counter
    contributor_lines: Counter
    now: float

    def hotspot_ranking(self) -> list[dict]:
        max_commits = max((f.commits for f in self.files.values()), default=1) or 1
        max_loc = max((f.loc for f in self.files.values()), default=1) or 1
        scored = []
        for f in self.files.values():
            if f.loc == 0:
                continue
            score = (f.commits / max_commits) * (math.log1p(f.loc) / math.log1p(max_loc))
            author, share = f.main_author
            scored.append({
                "path": f.path, "score": round(score, 4), "commits": f.commits,
                "loc": f.loc, "churn": f.churn, "authors": len(f.authors),
                "main_author": author, "main_author_share": round(share, 3),
                "last_change": f.last_seen,
            })
        scored.sort(key=lambda r: (-r["score"], -r["commits"], r["path"]))
        return scored

    def coupling_degree(self, a: str, b: str) -> tuple[int, float]:
        key = (a, b) if a < b else (b, a)
        shared = self.pair_counts.get(key, 0)
        if not shared:
            return 0, 0.0
        degree = shared / ((self.files[a].commits + self.files[b].commits) / 2)
        return shared, round(min(degree, 1.0), 3)


def collect(repo: str = ".", since: str | None = None, paths: list[str] | None = None,
            excludes: list[str] | None = None, use_default_excludes: bool = True,
            include_bots: bool = False, now: float | None = None,
            rev: str = "HEAD") -> History:
    """Mine history reachable from `rev`. Files are those tracked at HEAD."""
    root = run_git(["rev-parse", "--show-toplevel"], repo).strip()
    try:
        run_git(["rev-parse", "--verify", "-q", "HEAD"], root)
    except GitError:
        raise GitError("repository has no commits yet") from None
    patterns = (DEFAULT_EXCLUDES if use_default_excludes else []) + list(excludes or [])
    now = time.time() if now is None else now

    commits = read_log(root, since=since, paths=paths, rev=rev)
    if not include_bots:
        commits = [c for c in commits if not is_bot(c.author)]
    alive = {p for p in tracked_files(root) if not is_excluded(p, patterns)}

    files: dict[str, FileStats] = {}
    contributor_commits: Counter = Counter()
    contributor_lines: Counter = Counter()
    pair_counts: Counter = Counter()

    author_last: dict[str, int] = {}
    for c in commits:
        contributor_commits[c.author] += 1
        author_last[c.author] = max(author_last.get(c.author, 0), c.timestamp)
        touched = set()
        for path, added, deleted in c.changes:
            if path not in alive:
                continue
            fs = files.get(path)
            if fs is None:
                fs = files[path] = FileStats(path, first_seen=c.timestamp, last_seen=c.timestamp)
            if path not in touched:
                fs.commits += 1
            fs.added += added
            fs.deleted += deleted
            fs.first_seen = min(fs.first_seen, c.timestamp)
            fs.last_seen = max(fs.last_seen, c.timestamp)
            fs.authors[c.author] += max(added + deleted, 1)
            contributor_lines[c.author] += added + deleted
            touched.add(path)
        if 2 <= len(touched) <= MAX_FILES_FOR_COUPLING:
            for a, b in combinations(sorted(touched), 2):
                pair_counts[(a, b)] += 1

    for fs in files.values():
        fs.loc = count_loc(root, fs.path)

    return History(root, commits, files, pair_counts, author_last,
                   contributor_commits, contributor_lines, now)


def analyze(repo: str = ".", since: str | None = None, paths: list[str] | None = None,
            excludes: list[str] | None = None, use_default_excludes: bool = True,
            top: int = 10, depth: int = 1, min_coupling: int = 5,
            stale_days: int = 365, include_bots: bool = False,
            now: float | None = None, history: History | None = None) -> Report:
    h = history or collect(repo, since=since, paths=paths, excludes=excludes,
                           use_default_excludes=use_default_excludes,
                           include_bots=include_bots, now=now)
    root, commits, files, now = h.root, h.commits, h.files, h.now
    pair_counts, author_last = h.pair_counts, h.author_last
    contributor_commits, contributor_lines = h.contributor_commits, h.contributor_lines

    # Hotspots: change frequency x (log) size, both normalised to the repo max.
    scored = h.hotspot_ranking()

    # Ownership / bus factor per directory.
    dir_authors: dict[str, Counter] = defaultdict(Counter)
    dir_files: Counter = Counter()
    dir_loc: Counter = Counter()
    for f in files.values():
        d = directory_of(f.path, depth)
        dir_authors[d].update(f.authors)
        dir_files[d] += 1
        dir_loc[d] += f.loc
    ownership = []
    for d, authors in dir_authors.items():
        total = sum(authors.values()) or 1
        main, lines = authors.most_common(1)[0]
        ownership.append({
            "directory": d, "files": dir_files[d], "loc": dir_loc[d],
            "bus_factor": bus_factor(authors), "authors": len(authors),
            "main_author": main, "main_author_share": round(lines / total, 3),
        })
    ownership.sort(key=lambda r: (r["bus_factor"], -r["loc"], r["directory"]))

    # Temporal coupling: files that keep changing in the same commits.
    coupling = []
    for (a, b), shared in pair_counts.items():
        if shared < min_coupling:
            continue
        degree = shared / ((files[a].commits + files[b].commits) / 2)
        coupling.append({"a": a, "b": b, "shared_commits": shared,
                         "degree": round(min(degree, 1.0), 3)})
    coupling.sort(key=lambda r: (-r["degree"], -r["shared_commits"], r["a"], r["b"]))

    # Stale code: sizeable files nobody has touched in a long time.
    cutoff = now - stale_days * 86400
    stale = [
        {"path": f.path, "loc": f.loc, "last_change": f.last_seen,
         "days": int((now - f.last_seen) // 86400), "main_author": f.main_author[0]}
        for f in files.values() if f.last_seen < cutoff and f.loc > 0
    ]
    stale.sort(key=lambda r: (-r["loc"], -r["days"], r["path"]))

    # Orphaned code: the person who wrote most of it is no longer active.
    orphaned = []
    for f in files.values():
        if f.loc == 0:
            continue
        author, share = f.main_author
        last = author_last.get(author, 0)
        if share >= 0.5 and last < cutoff:
            orphaned.append({
                "path": f.path, "loc": f.loc, "main_author": author,
                "main_author_share": round(share, 3), "author_last_seen": last,
                "author_inactive_days": int((now - last) // 86400),
            })
    orphaned.sort(key=lambda r: (-r["loc"], r["path"]))

    contributors = [
        {"name": name, "commits": n, "lines": contributor_lines[name]}
        for name, n in contributor_commits.most_common(top)
    ]

    ts = [c.timestamp for c in commits]
    return Report(
        repo=os.path.basename(root), commits=len(commits), authors=len(contributor_commits),
        first_ts=min(ts, default=0), last_ts=max(ts, default=0), files=len(files),
        hotspots=scored[:top], ownership=ownership[:top], coupling=coupling[:top],
        stale=stale[:top], orphaned=orphaned[:top], contributors=contributors,
    )

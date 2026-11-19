"""Pull-request review: what does history say about the files this change touches?"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass

from .analysis import GitError, History, collect, run_git

# A changed file ranking in the top HOTSPOT_TOP of the repo counts as a hotspot,
# or in the top HOTSPOT_SHARE of files for big repos, whichever is larger.
HOTSPOT_TOP = 10
HOTSPOT_SHARE = 0.05
# ...and its hotspot score (1.0 = the repo's worst file) must be at least this.
HOTSPOT_MIN_SCORE = 0.25
# Touching a top-3 hotspot means "high" risk only in repos at least this big;
# in a 7-file repo being #2 says very little.
HIGH_RISK_MIN_FILES = 20

# A file B is "probably missing" from a change touching A when A and B changed
# together at least MISSING_MIN_SHARED times and in at least MISSING_MIN_DEGREE
# of their commits.
MISSING_MIN_SHARED = 3
MISSING_MIN_DEGREE = 0.6
# Leaving out a partner this tightly coupled is enough on its own for "high" risk.
HIGH_DEGREE = 0.9
HIGH_MIN_SHARED = 5


@dataclass
class Review:
    repo: str
    base: str
    commits: int
    changed: list[dict]       # every changed file that has history
    new_files: list[str]      # changed files without any history yet
    hotspots: list[dict]      # changed files that are repo hotspots
    missing: list[dict]       # co-change partners left untouched
    experts: list[dict]       # people who know the changed files best
    orphaned: list[dict]      # changed files whose main author went quiet
    risk: str                 # "high" | "medium" | "low"
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "repo": self.repo, "base": self.base, "history_commits": self.commits,
            "risk": self.risk, "reasons": self.reasons,
            "hotspots": self.hotspots, "missing_cochanges": self.missing,
            "experts": self.experts, "orphaned": self.orphaned,
            "changed": self.changed, "new_files": self.new_files,
        }


def changed_files(root: str, base: str) -> list[str]:
    """Files changed on this branch since it forked from `base` (like a PR diff)."""
    try:
        run_git(["rev-parse", "--verify", "-q", f"{base}^{{commit}}"], root)
    except GitError:
        raise GitError(
            f"unknown base '{base}'. Use a branch, tag or commit, e.g. origin/main. "
            "In CI, check out full history (actions/checkout with fetch-depth: 0)."
        ) from None
    try:
        out = run_git(["-c", "core.quotepath=off", "diff", "--name-only",
                       "--diff-filter=ACMR", f"{base}...HEAD"], root)
    except GitError as exc:
        raise GitError(
            f"cannot find where this branch forked from '{base}' ({exc}). "
            "In CI, check out full history (actions/checkout with fetch-depth: 0)."
        ) from None
    return [line for line in out.splitlines() if line]


def review(repo: str = ".", base: str = "origin/main", since: str | None = None,
           excludes: list[str] | None = None, use_default_excludes: bool = True,
           include_bots: bool = False, stale_days: int = 365, now: float | None = None,
           history: History | None = None, changed: list[str] | None = None) -> Review:
    if changed is None:
        root = run_git(["rev-parse", "--show-toplevel"], repo).strip()
        changed = changed_files(root, base)
    # History up to the base branch only: the PR's own commits should not count.
    h = history or collect(repo, since=since, excludes=excludes,
                           use_default_excludes=use_default_excludes,
                           include_bots=include_bots, now=now, rev=base)
    changed_set = set(changed)

    ranking = h.hotspot_ranking()
    rank_of = {r["path"]: i + 1 for i, r in enumerate(ranking)}
    by_path = {r["path"]: r for r in ranking}
    hot_cutoff = max(HOTSPOT_TOP, int(len(ranking) * HOTSPOT_SHARE))

    rows, new_files = [], []
    for path in changed:
        if path not in h.files:
            new_files.append(path)
            continue
        f = h.files[path]
        author, share = f.main_author
        rows.append({
            "path": path, "rank": rank_of.get(path), "of": len(ranking),
            "commits": f.commits, "loc": f.loc, "authors": len(f.authors),
            "main_author": author, "main_author_share": round(share, 3),
            "score": by_path[path]["score"] if path in by_path else 0.0,
        })
    rows.sort(key=lambda r: (r["rank"] or 10**9, r["path"]))
    hotspots = [r for r in rows if r["rank"] and r["rank"] <= hot_cutoff
                and r["score"] >= HOTSPOT_MIN_SCORE]

    # Co-change partners that this change leaves alone.
    missing: dict[str, dict] = {}
    for (a, b), shared in h.pair_counts.items():
        if shared < MISSING_MIN_SHARED:
            continue
        for mine, other in ((a, b), (b, a)):
            if mine in changed_set and other not in changed_set:
                _, degree = h.coupling_degree(mine, other)
                if degree < MISSING_MIN_DEGREE:
                    continue
                best = missing.get(other)
                if best is None or (degree, shared) > (best["degree"], best["shared_commits"]):
                    missing[other] = {"file": other, "changed_with": mine,
                                      "degree": degree, "shared_commits": shared}
    missing_list = sorted(missing.values(),
                          key=lambda r: (-r["degree"], -r["shared_commits"], r["file"]))

    # Who knows these files? Weight by lines each author changed in them.
    knowledge: Counter = Counter()
    for path in changed:
        if path in h.files:
            knowledge.update(h.files[path].authors)
    total = sum(knowledge.values()) or 1
    experts = [{"name": n, "share": round(v / total, 3)}
               for n, v in knowledge.most_common(3) if v / total >= 0.05]

    cutoff = h.now - stale_days * 86400
    orphaned = []
    for r in rows:
        last = h.author_last.get(r["main_author"], 0)
        if r["main_author_share"] >= 0.5 and last < cutoff:
            orphaned.append({"path": r["path"], "main_author": r["main_author"],
                             "author_inactive_days": int((h.now - last) // 86400)})

    def files(n: int) -> str:
        return "1 file" if n == 1 else f"{n} files"

    # Each reason completes the sentence "This PR ...".
    reasons = []
    top_hot = [r for r in hotspots if r["rank"] <= 3 and len(ranking) >= HIGH_RISK_MIN_FILES]
    strong_missing = [m for m in missing_list
                      if m["degree"] >= HIGH_DEGREE and m["shared_commits"] >= HIGH_MIN_SHARED]
    if top_hot:
        extra = len(hotspots) - 1
        reasons.append(f"touches the repo's #{top_hot[0]['rank']} hotspot"
                       + (f" and {extra} more" if extra else ""))
    elif hotspots:
        reasons.append(f"touches {len(hotspots)} hotspot file" + ("s" if len(hotspots) > 1 else ""))
    if missing_list:
        n = len(missing_list)
        reasons.append(f"skips {files(n)} that usually {'changes' if n == 1 else 'change'}"
                       " together with the files it edits")
    if orphaned:
        reasons.append(f"edits {files(len(orphaned))} whose main author is no longer active")

    if top_hot or strong_missing:
        risk = "high"
    elif hotspots or missing_list or orphaned:
        risk = "medium"
    else:
        risk = "low"

    return Review(
        repo=os.path.basename(h.root), base=base, commits=len(h.commits),
        changed=rows, new_files=new_files, hotspots=hotspots, missing=missing_list[:10],
        experts=experts, orphaned=orphaned, risk=risk, reasons=reasons,
    )

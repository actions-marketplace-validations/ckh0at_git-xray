"""Command line entry point: `git-xray` (also usable as `git xray`)."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .analysis import GitError, analyze
from .render import render_markdown, render_terminal, use_color


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="git-xray",
        description="X-ray a git repository: hotspots, bus factor, hidden coupling, stale code.",
        epilog="Examples:\n"
               "  git xray                       # analyse the current repo\n"
               "  git xray --since '6 months ago'\n"
               "  git xray src/ --top 20\n"
               "  git xray --md > XRAY.md        # markdown for PRs / wikis\n"
               "  git xray --json | jq .hotspots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("paths", nargs="*", help="limit analysis to these paths")
    ap.add_argument("-C", dest="repo", default=".", metavar="DIR",
                    help="run as if started in DIR (like git -C)")
    ap.add_argument("--since", metavar="WHEN",
                    help="only consider commits newer than WHEN, e.g. '1 year ago', 2024-01-01")
    ap.add_argument("-n", "--top", type=int, default=10, help="rows per section (default 10)")
    ap.add_argument("--depth", type=int, default=1,
                    help="directory depth for bus-factor grouping (default 1)")
    ap.add_argument("--min-coupling", type=int, default=5, metavar="N",
                    help="minimum shared commits to report a coupling (default 5)")
    ap.add_argument("--stale-days", type=int, default=365, metavar="N",
                    help="a file is stale after N days without changes (default 365)")
    ap.add_argument("-x", "--exclude", action="append", default=[], metavar="GLOB",
                    help="exclude files matching GLOB (repeatable)")
    ap.add_argument("--no-default-excludes", action="store_true",
                    help="do not skip lockfiles, vendored and generated files")
    ap.add_argument("--include-bots", action="store_true",
                    help="count commits from dependabot, renovate and other bots")
    fmt = ap.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="machine readable output")
    fmt.add_argument("--md", "--markdown", dest="md", action="store_true",
                     help="markdown output")
    ap.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    ap.add_argument("-V", "--version", action="version", version=f"git-xray {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        report = analyze(
            args.repo, since=args.since, paths=args.paths or None, excludes=args.exclude,
            use_default_excludes=not args.no_default_excludes, top=args.top,
            depth=args.depth, min_coupling=args.min_coupling, stale_days=args.stale_days,
            include_bots=args.include_bots,
        )
    except GitError as exc:
        print(f"git-xray: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    elif args.md:
        print(render_markdown(report))
    else:
        force = {"always": True, "never": False}.get(args.color)
        print(render_terminal(report, color=use_color(sys.stdout, force)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

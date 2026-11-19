"""Command line entry point: `git-xray` (also usable as `git xray`)."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .analysis import GitError, analyze
from .render import (ICON_MODES, detect_icons, render_markdown, render_review_markdown,
                     render_review_terminal, render_terminal, use_color)
from .review import review


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="git-xray",
        description="X-ray a git repository: hotspots, bus factor, hidden coupling, stale code.",
        epilog="Examples:\n"
               "  git xray                       # analyse the current repo\n"
               "  git xray --since '6 months ago'\n"
               "  git xray src/ --top 20\n"
               "  git xray --md > XRAY.md        # markdown for PRs / wikis\n"
               "  git xray --json | jq .hotspots\n"
               "  git xray --diff origin/main    # PR check: risks in this branch's changes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("paths", nargs="*", help="limit analysis to these paths")
    ap.add_argument("--diff", metavar="BASE",
                    help="PR check: report risks in the files changed since BASE "
                         "(e.g. origin/main) instead of a whole-repo report")
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
    ap.add_argument("--icons", choices=["auto", *ICON_MODES], default="auto",
                    help="emoji, plain (no emoji) or ascii. Use plain/ascii if you see "
                         "garbled symbols. Also settable via GIT_XRAY_ICONS")
    ap.add_argument("-V", "--version", action="version", version=f"git-xray {__version__}")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        if stream.isatty():
            # Keep the console's own encoding (e.g. GBK); forcing UTF-8 there
            # turns every non-ASCII glyph into mojibake. Never crash, though.
            stream.reconfigure(errors="replace")
        else:
            # Files and pipes: write UTF-8 so --md / --json are portable.
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.diff:
        return run_review(args)
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
        icons = args.icons if args.icons != "auto" else detect_icons(sys.stdout)
        print(render_terminal(report, color=use_color(sys.stdout, force), icons=icons))
    return 0


def run_review(args) -> int:
    try:
        rv = review(args.repo, base=args.diff, since=args.since, excludes=args.exclude,
                    use_default_excludes=not args.no_default_excludes,
                    include_bots=args.include_bots, stale_days=args.stale_days)
    except GitError as exc:
        print(f"git-xray: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rv.to_dict(), indent=2, ensure_ascii=False))
    elif args.md:
        print(render_review_markdown(rv))
    else:
        force = {"always": True, "never": False}.get(args.color)
        icons = args.icons if args.icons != "auto" else detect_icons(sys.stdout)
        print(render_review_terminal(rv, color=use_color(sys.stdout, force), icons=icons))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

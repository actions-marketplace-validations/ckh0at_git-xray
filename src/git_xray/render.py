"""Render a Report as a colourful terminal page or as Markdown."""

from __future__ import annotations

import os
import shutil
import sys
import time

from .analysis import Report

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"

BAR_BLOCKS = " ▏▎▍▌▋▊▉█"


def use_color(stream=sys.stdout, force: bool | None = None) -> bool:
    if force is not None:
        return force
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class Painter:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        return "".join(styles) + text + RESET


def bar(fraction: float, width: int) -> str:
    fraction = max(0.0, min(1.0, fraction))
    total = fraction * width
    full = int(total)
    rest = int((total - full) * 8)
    out = "█" * full
    if full < width and rest:
        out += BAR_BLOCKS[rest]
    return out.ljust(width)


def heat_color(fraction: float) -> str:
    if fraction >= 0.66:
        return RED
    if fraction >= 0.33:
        return YELLOW
    return GREEN


def ago(ts: int, now: float) -> str:
    days = int((now - ts) // 86400)
    if days < 1:
        return "today"
    if days < 60:
        return f"{days}d ago"
    if days < 730:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def shorten(path: str, width: int) -> str:
    if len(path) <= width:
        return path
    keep = width - 1
    return "…" + path[-keep:]


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def render_terminal(report: Report, color: bool = True, width: int | None = None,
                    now: float | None = None) -> str:
    p = Painter(color)
    now = time.time() if now is None else now
    width = width or min(shutil.get_terminal_size((100, 24)).columns, 120)
    path_w = max(24, width - 52)
    lines: list[str] = []
    add = lines.append

    def header(icon: str, title: str, hint: str) -> None:
        add("")
        add(p(f"{icon} {title}", BOLD) + "  " + p(hint, DIM))
        add(p("─" * min(width, 100), DIM))

    span = ""
    if report.first_ts:
        span = f"{time.strftime('%Y-%m-%d', time.localtime(report.first_ts))} → " \
               f"{time.strftime('%Y-%m-%d', time.localtime(report.last_ts))}"
    add("")
    add(p(" git-xray ", BOLD, "\x1b[7m") + "  " + p(report.repo, BOLD, CYAN))
    add(p(f"{report.commits} commits · {report.authors} authors · "
          f"{report.files} files · {span}", DIM))

    # Hotspots
    header("🔥", "Hotspots", "files that change often AND are big. Refactor these first.")
    if not report.hotspots:
        add(p("  no history to analyse", DIM))
    for i, h in enumerate(report.hotspots, 1):
        frac = h["score"]
        rank = p("%2d" % i, DIM)
        heat = p(bar(frac, 10), heat_color(frac))
        name = shorten(h["path"], path_w).ljust(path_w)
        commits = p("%4d commits" % h["commits"], BOLD)
        loc = p("%6s loc" % human(h["loc"]), DIM)
        authors = p("%2d authors" % h["authors"], DIM)
        add(" ".join([rank, heat, name, commits, loc, authors]))

    # Ownership
    header("🚌", "Bus factor", "how many people must leave before nobody knows this code.")
    for o in report.ownership:
        bf = o["bus_factor"]
        style = RED if bf <= 1 else YELLOW if bf == 2 else GREEN
        share = o["main_author_share"]
        warn = p(" ⚠", RED, BOLD) if bf <= 1 and o["authors"] > 1 else "  "
        name = shorten(o["directory"] + "/", path_w).ljust(path_w)
        add("  " + p("bus=%d" % bf, style, BOLD) + warn + " " + name + " "
            + p(bar(share, 8), style) + " %4.0f%% " % (share * 100)
            + p(o["main_author"], MAGENTA))

    # Coupling
    header("🔗", "Hidden coupling", "files that always change together. Missing abstraction?")
    if not report.coupling:
        add(p("  nothing above threshold (use --min-coupling to lower it)", DIM))
    half = max(16, (path_w + 10) // 2)
    for c in report.coupling:
        degree = p("%3.0f%%" % (c["degree"] * 100), BOLD, heat_color(c["degree"]))
        add("  " + degree + " " + shorten(c["a"], half) + " " + p("⇄", CYAN) + " "
            + shorten(c["b"], half) + " " + p("(%d commits)" % c["shared_commits"], DIM))

    # Stale
    header("🧊", "Stale code", "big files nobody has touched in a long time.")
    if not report.stale:
        add(p("  nothing stale. Everything was touched recently.", DIM))
    for s in report.stale:
        when = p("%8s" % ago(s["last_change"], now), BLUE)
        name = shorten(s["path"], path_w).ljust(path_w)
        add("  " + when + " " + name + " " + p("%6s loc" % human(s["loc"]), DIM) + " "
            + p(s["main_author"], MAGENTA))

    # Orphaned
    header("👻", "Orphaned code", "main author has gone quiet. Knowledge may have left with them.")
    if not report.orphaned:
        add(p("  no orphaned files. Main authors are still around.", DIM))
    for o in report.orphaned:
        gone = p("%8s" % ("%dd" % o["author_inactive_days"] if o["author_inactive_days"] < 730
                          else "%dy" % (o["author_inactive_days"] // 365)), BLUE)
        name = shorten(o["path"], path_w).ljust(path_w)
        add("  " + gone + " " + name + " " + p("%6s loc" % human(o["loc"]), DIM) + " "
            + p(o["main_author"], MAGENTA) + p(" (%d%%)" % round(o["main_author_share"] * 100), DIM))

    # Contributors
    header("👥", "Top contributors", "by commits in the analysed range.")
    max_c = max((c["commits"] for c in report.contributors), default=1) or 1
    for c in report.contributors:
        add("  " + p(bar(c["commits"] / max_c, 16), CYAN) + " %5d  " % c["commits"]
            + c["name"] + " " + p("(±%s lines)" % human(c["lines"]), DIM))
    add("")
    return "\n".join(lines)


def render_markdown(report: Report, now: float | None = None) -> str:
    now = time.time() if now is None else now
    out = [f"# git-xray report: `{report.repo}`", "",
           f"{report.commits} commits · {report.authors} authors · {report.files} files", ""]

    out += ["## 🔥 Hotspots", "", "| # | File | Score | Commits | LOC | Authors |",
            "|---|------|-------|---------|-----|---------|"]
    for i, h in enumerate(report.hotspots, 1):
        out.append(f"| {i} | `{h['path']}` | {h['score']:.2f} | {h['commits']} | "
                   f"{h['loc']} | {h['authors']} |")

    out += ["", "## 🚌 Bus factor", "", "| Directory | Bus factor | Main author | Share |",
            "|-----------|------------|-------------|-------|"]
    for o in report.ownership:
        out.append(f"| `{o['directory']}/` | {o['bus_factor']} | {o['main_author']} | "
                   f"{o['main_author_share']:.0%} |")

    out += ["", "## 🔗 Hidden coupling", ""]
    if report.coupling:
        out += ["| File A | File B | Degree | Shared commits |",
                "|--------|--------|--------|----------------|"]
        for c in report.coupling:
            out.append(f"| `{c['a']}` | `{c['b']}` | {c['degree']:.0%} | {c['shared_commits']} |")
    else:
        out.append("_Nothing above threshold._")

    out += ["", "## 🧊 Stale code", ""]
    if report.stale:
        out += ["| File | LOC | Last change | Main author |", "|------|-----|-------------|-------------|"]
        for s in report.stale:
            out.append(f"| `{s['path']}` | {s['loc']} | {ago(s['last_change'], now)} | "
                       f"{s['main_author']} |")
    else:
        out.append("_Nothing stale._")

    out += ["", "## 👻 Orphaned code", ""]
    if report.orphaned:
        out += ["| File | LOC | Main author | Share | Author last seen |",
                "|------|-----|-------------|-------|------------------|"]
        for o in report.orphaned:
            out.append(f"| `{o['path']}` | {o['loc']} | {o['main_author']} | "
                       f"{o['main_author_share']:.0%} | {ago(o['author_last_seen'], now)} |")
    else:
        out.append("_No orphaned files._")
    out.append("")
    return "\n".join(out)

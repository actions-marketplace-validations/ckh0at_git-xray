"""Turn ANSI-coloured terminal output (stdin) into a terminal-window SVG (stdout).

Used to regenerate docs/demo.svg:
    FORCE_COLOR=1 git-xray -C path/to/flask -n 6 | python scripts/ansi2svg.py > docs/demo.svg
"""

import html
import re
import sys
import unicodedata

PALETTE = {
    31: "#ff6b6b", 32: "#69db7c", 33: "#ffd43b", 34: "#74c0fc",
    35: "#e599f7", 36: "#66d9e8",
}
FG = "#e6edf3"
DIM_FG = "#7d8590"
BG = "#0d1117"
CHAR_W = 8.4
LINE_H = 19
PAD_X = 20
TOP = 46

SGR = re.compile(r"\x1b\[([0-9;]*)m")


def cell_width(ch: str) -> int:
    if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) >= 0x1F300:
        return 2
    return 1


def parse(line: str):
    """Yield (text, style) runs."""
    style = {"fg": None, "bold": False, "dim": False, "inv": False}
    pos = 0
    for m in SGR.finditer(line):
        if m.start() > pos:
            yield line[pos:m.start()], dict(style)
        for code in (int(c) for c in (m.group(1) or "0").split(";") if c):
            if code == 0:
                style = {"fg": None, "bold": False, "dim": False, "inv": False}
            elif code == 1:
                style["bold"] = True
            elif code == 2:
                style["dim"] = True
            elif code == 7:
                style["inv"] = True
            elif code in PALETTE:
                style["fg"] = PALETTE[code]
        pos = m.end()
    if pos < len(line):
        yield line[pos:], dict(style)


def main() -> None:
    lines = sys.stdin.read().rstrip("\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    cols = max(sum(cell_width(c) for c in SGR.sub("", ln)) for ln in lines)
    width = int(cols * CHAR_W + PAD_X * 2)
    height = TOP + len(lines) * LINE_H + 16

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="ui-monospace,SFMono-Regular,Menlo,'
        f'Consolas,monospace" font-size="14">',
        f'<rect width="{width}" height="{height}" rx="10" fill="{BG}"/>',
        '<circle cx="20" cy="18" r="6" fill="#ff5f57"/>',
        '<circle cx="40" cy="18" r="6" fill="#febc2e"/>',
        '<circle cx="60" cy="18" r="6" fill="#28c840"/>',
        f'<text x="{width / 2}" y="22" fill="{DIM_FG}" text-anchor="middle" '
        f'font-size="12">~/flask — git xray</text>',
    ]
    for i, line in enumerate(lines):
        y = TOP + i * LINE_H + 12
        x_cells = 0
        for text, st in parse(line):
            if not text:
                continue
            w = sum(cell_width(c) for c in text)
            x = PAD_X + x_cells * CHAR_W
            fg = st["fg"] or (DIM_FG if st["dim"] else FG)
            if st["inv"]:
                out.append(f'<rect x="{x:.1f}" y="{y - 14}" width="{w * CHAR_W:.1f}" '
                           f'height="{LINE_H}" fill="{FG}"/>')
                fg = BG
            attrs = f'fill="{fg}"'
            if st["bold"]:
                attrs += ' font-weight="bold"'
            if st["dim"] and st["fg"]:
                attrs += ' opacity="0.65"'
            out.append(f'<text x="{x:.1f}" y="{y}" {attrs} xml:space="preserve" '
                       f'textLength="{w * CHAR_W:.1f}" lengthAdjust="spacingAndGlyphs">'
                       f'{html.escape(text)}</text>')
            x_cells += w
    out.append("</svg>")
    print("\n".join(out))


if __name__ == "__main__":
    main()

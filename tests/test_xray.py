import json
import os
import subprocess

import pytest

from git_xray.analysis import analyze, bus_factor, is_excluded, parse_numstat_path
from git_xray.cli import main
from git_xray.render import render_markdown, render_terminal

DAY = 86400
T0 = 1_600_000_000  # fixed epoch so tests are deterministic


class Repo:
    def __init__(self, path):
        self.path = str(path)
        self.t = T0
        self.git("init", "-q", "-b", "main")

    def git(self, *args, author="Alice", ts=None):
        env = dict(os.environ)
        ts = self.t if ts is None else ts
        env.update({
            "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author.lower()}@x.io",
            "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": f"{author.lower()}@x.io",
            "GIT_AUTHOR_DATE": f"@{ts} +0000", "GIT_COMMITTER_DATE": f"@{ts} +0000",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        })
        subprocess.run(["git", *args], cwd=self.path, env=env, check=True,
                       capture_output=True)

    def commit(self, files, author="Alice", days_later=1, msg="change"):
        self.t += days_later * DAY
        for name, content in files.items():
            full = os.path.join(self.path, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg, author=author)


def lines(n, tag="x"):
    return "".join(f"{tag} line {i}\n" for i in range(n))


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def test_parse_numstat_paths():
    assert parse_numstat_path("a/b.py") == (None, "a/b.py")
    assert parse_numstat_path("old.py => new.py") == ("old.py", "new.py")
    assert parse_numstat_path("src/{a => b}/x.py") == ("src/a/x.py", "src/b/x.py")
    assert parse_numstat_path("src/{ => sub}/x.py") == ("src/x.py", "src/sub/x.py")


def test_bus_factor():
    from collections import Counter
    assert bus_factor(Counter()) == 0
    assert bus_factor(Counter(a=90, b=10)) == 1
    assert bus_factor(Counter(a=40, b=35, c=25)) == 2


def test_excludes():
    assert is_excluded("package-lock.json", ["package-lock.json"])
    assert is_excluded("web/node_modules/x.js", ["node_modules/*"])
    assert is_excluded("CHANGELOG.md", ["CHANGELOG*"])
    assert not is_excluded("src/main.py", ["vendor/*", "*.lock"])


def test_hotspot_ranks_frequent_big_file_first(repo):
    repo.commit({"core.py": lines(300), "util.py": lines(10)})
    for i in range(8):
        repo.commit({"core.py": lines(300 + i, tag=str(i))})
    repo.commit({"util.py": lines(12)})
    r = analyze(repo.path, now=repo.t)
    assert r.hotspots[0]["path"] == "core.py"
    assert r.hotspots[0]["commits"] == 9
    assert r.hotspots[0]["score"] == 1.0


def test_renames_follow_history(repo):
    repo.commit({"old.py": lines(50)})
    repo.commit({"old.py": lines(51)})
    repo.git("mv", "old.py", "new.py")
    repo.t += DAY
    repo.git("commit", "-q", "-m", "rename")
    repo.commit({"new.py": lines(52)})
    r = analyze(repo.path, now=repo.t)
    paths = {h["path"]: h for h in r.hotspots}
    assert "old.py" not in paths
    assert paths["new.py"]["commits"] == 4


def test_deleted_files_are_ignored(repo):
    repo.commit({"a.py": lines(10), "gone.py": lines(10)})
    os.remove(os.path.join(repo.path, "gone.py"))
    repo.commit({"a.py": lines(11)})
    r = analyze(repo.path, now=repo.t)
    assert [h["path"] for h in r.hotspots] == ["a.py"]


def test_bus_factor_flags_single_owner(repo):
    repo.commit({"solo/a.py": lines(100), "team/b.py": lines(100)}, author="Alice")
    repo.commit({"team/b.py": lines(100) + lines(100, "b")}, author="Bob")
    repo.commit({"team/b.py": lines(100) + lines(100, "b") + lines(100, "c")}, author="Carol")
    r = analyze(repo.path, now=repo.t)
    by_dir = {o["directory"]: o for o in r.ownership}
    assert by_dir["solo"]["bus_factor"] == 1
    assert by_dir["solo"]["main_author"] == "Alice"
    assert by_dir["team"]["bus_factor"] >= 2


def test_coupling_detects_files_changing_together(repo):
    repo.commit({"api.py": lines(10), "schema.py": lines(10), "other.py": lines(10)})
    for i in range(6):
        repo.commit({"api.py": lines(10, str(i)), "schema.py": lines(10, str(i))})
    repo.commit({"other.py": lines(11)})
    r = analyze(repo.path, now=repo.t, min_coupling=5)
    assert r.coupling, "expected a coupling pair"
    top = r.coupling[0]
    assert {top["a"], top["b"]} == {"api.py", "schema.py"}
    assert top["shared_commits"] == 7
    assert top["degree"] == 1.0


def test_stale_and_orphaned(repo):
    repo.commit({"legacy.py": lines(200)}, author="Dana")
    repo.commit({"fresh.py": lines(50)}, author="Eve", days_later=800)
    r = analyze(repo.path, now=repo.t, stale_days=365)
    assert [s["path"] for s in r.stale] == ["legacy.py"]
    assert r.stale[0]["days"] == 800
    assert [o["path"] for o in r.orphaned] == ["legacy.py"]
    assert r.orphaned[0]["main_author"] == "Dana"


def test_bots_are_skipped_by_default(repo):
    repo.commit({"a.py": lines(10)}, author="Alice")
    repo.commit({"a.py": lines(20)}, author="dependabot[bot]")
    assert analyze(repo.path, now=repo.t).authors == 1
    assert analyze(repo.path, now=repo.t, include_bots=True).authors == 2


def test_since_filters_history(repo):
    repo.commit({"a.py": lines(10)})
    repo.commit({"a.py": lines(11)}, days_later=100)
    r = analyze(repo.path, since=f"@{repo.t - 10 * DAY}", now=repo.t)
    assert r.commits == 1


def test_renderers_produce_all_sections(repo):
    repo.commit({"a.py": lines(10), "b.py": lines(10)})
    repo.commit({"a.py": lines(11)}, author="Bob")
    r = analyze(repo.path, now=repo.t)
    term = render_terminal(r, color=False, width=100, now=repo.t)
    for title in ("Hotspots", "Bus factor", "Hidden coupling", "Stale code",
                  "Orphaned code", "Top contributors"):
        assert title in term
    assert "\x1b[" not in term
    assert "\x1b[" in render_terminal(r, color=True, width=100, now=repo.t)
    md = render_markdown(r, now=repo.t)
    assert md.startswith("# git-xray report")
    assert "| `a.py` |" in md


def test_cli_json_and_errors(repo, capsys, tmp_path_factory):
    assert main(["-C", repo.path]) == 2  # no commits yet
    assert "no commits" in capsys.readouterr().err
    repo.commit({"a.py": lines(10)})
    assert main(["-C", repo.path, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["commits"] == 1
    assert data["hotspots"][0]["path"] == "a.py"
    not_a_repo = tmp_path_factory.mktemp("plain")
    assert main(["-C", str(not_a_repo)]) == 2


class FakeStream:
    def __init__(self, encoding):
        self.encoding = encoding


def test_detect_icons():
    from git_xray.render import detect_icons

    def d(enc, env, plat="linux", pref="UTF-8"):
        return detect_icons(FakeStream(enc), env, plat, preferred_encoding=pref)

    en = {"LANG": "en_US.UTF-8", "TERM": "xterm-256color"}
    assert d("utf-8", en) == "emoji"
    assert d("ascii", en) == "ascii"
    assert d("cp1252", {}, "win32", "cp1252") == "ascii"
    assert d("utf-8", {}, "win32", "cp1252") == "plain"  # old conhost
    assert d("utf-8", {"WT_SESSION": "1"}, "win32", "cp1252") == "emoji"
    assert d("utf-8", {"GIT_XRAY_ICONS": "plain", "LANG": "ja_JP.UTF-8"}) == "plain"
    assert d("utf-8", {"LANG": "en_US.UTF-8", "TERM": "linux"}) == "plain"
    # an explicit non-CJK LC_CTYPE still overrides a CJK LANG
    assert d("utf-8", {"LANG": "ja_JP.UTF-8", "LC_CTYPE": "en_US.UTF-8"}) == "emoji"
    assert d("utf-8", {"LC_CTYPE": "C.UTF-8"}) == "emoji"


@pytest.mark.parametrize("enc, env, plat, pref", [
    ("utf-8", {"LANG": "ja_JP.UTF-8", "TERM": "xterm-256color"}, "linux", "UTF-8"),
    ("utf-8", {"LC_ALL": "zh_CN.UTF-8"}, "linux", "UTF-8"),
    ("utf-8", {"LANG": "ko_KR.UTF-8"}, "darwin", "UTF-8"),
    ("cp932", {}, "win32", "cp932"),                      # Japanese cmd.exe
    ("utf-8", {"WT_SESSION": "1"}, "win32", "cp932"),     # Japanese Windows Terminal
    ("gbk", {}, "win32", "cp936"),                        # Chinese cmd.exe
    ("utf-8", {"TERM_PROGRAM": "mintty"}, "win32", "cp932"),  # Git Bash, JP Windows
    # ja_JP locale not installed: Python adds LC_CTYPE=C.UTF-8 (PEP 538)
    ("utf-8", {"LANG": "ja_JP.UTF-8", "LC_CTYPE": "C.UTF-8"}, "linux", "UTF-8"),
    ("utf-8", {"LANG": "zh_CN.UTF-8", "LC_ALL": "C"}, "linux", "UTF-8"),
])
def test_cjk_terminals_get_ascii(enc, env, plat, pref):
    from git_xray.render import detect_icons
    assert detect_icons(FakeStream(enc), env, plat, preferred_encoding=pref) == "ascii"


def cjk_width(text):
    """Display width in a CJK terminal: wide AND ambiguous chars take 2 cells."""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in text)


def test_ascii_output_aligns_in_cjk_terminal(tmp_path):
    """Simulate a Japanese terminal end to end and check nothing is double-wide."""
    import re
    import subprocess
    import sys

    (tmp_path / "jp").mkdir()
    r = Repo(tmp_path / "jp")
    r.commit({"src/core.py": lines(400), "src/api.py": lines(50)})
    for i in range(6):
        r.commit({"src/core.py": lines(400, str(i)), "src/api.py": lines(50, str(i))},
                 author="Bob" if i % 2 else "Alice")
    r.commit({"legacy/old.py": lines(300)}, author="Dana", days_later=1)
    r.commit({"src/core.py": lines(401)}, days_later=800)

    env = dict(os.environ, LANG="ja_JP.UTF-8", LC_ALL="", LC_CTYPE="",
               PYTHONIOENCODING="utf-8", COLUMNS="100")
    env.pop("GIT_XRAY_ICONS", None)
    env.pop("LC_ALL")
    env.pop("LC_CTYPE")
    code = ("import sys; sys.stdout.isatty = lambda: True; "
            "from git_xray.cli import main; sys.exit(main(sys.argv[1:]))")
    out = subprocess.run([sys.executable, "-c", code, "-C", r.path, "--color", "always"],
                         capture_output=True, env=env, check=True).stdout.decode("utf-8")
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert "Hotspots" in plain and "Orphaned code" in plain
    probe = ("import os, sys, locale; from git_xray import render; "
             "print({k: os.environ.get(k) for k in ('LANG', 'LC_ALL', 'LC_CTYPE', "
             "'GIT_XRAY_ICONS')}, sys.stdout.encoding, locale.getpreferredencoding(False), "
             "render.__file__, render.detect_icons(sys.stdout))")
    diag = subprocess.run([sys.executable, "-c", probe], capture_output=True, env=env,
                          text=True).stdout.strip()
    for line in plain.splitlines():
        assert cjk_width(line) == len(line), f"double-width glyph in: {line!r}\n{diag}"
        assert len(line) <= 100, f"line wider than terminal: {line!r}"


def test_icon_modes_render(repo):
    repo.commit({"a.py": lines(10), "b.py": lines(10)})
    r = analyze(repo.path, now=repo.t)
    plain = render_terminal(r, color=False, width=100, now=repo.t, icons="plain")
    assert "🔥" not in plain and "Hotspots" in plain and "█" in plain
    ascii_out = render_terminal(r, color=False, width=100, now=repo.t, icons="ascii")
    ascii_out.encode("ascii")  # must not raise
    assert "Hotspots" in ascii_out
    # Same column layout in every mode
    emoji_lines = render_terminal(r, color=False, width=100, now=repo.t).splitlines()
    assert len(emoji_lines) == len(ascii_out.splitlines())


def test_missing_directory_has_clear_error(tmp_path, capsys):
    assert main(["-C", str(tmp_path / "does-not-exist")]) == 2
    assert "no such directory" in capsys.readouterr().err

"""Broken-link check for the in-app Guide (``/guide``).

The Guide bundles the markdown listed in ``src/guide/content.ts``. These tests
mirror its page-slug and anchor rules and fail on:

- a relative link to a file that doesn't exist, or to a heading that doesn't;
- a ``/guide/...`` link (in the docs or in the app's code) to a missing page
  or heading;
- any other in-app link (``/discovery``) that matches no route in ``src/App.tsx``.
"""
import os
import re
from pathlib import Path

import pytest

from app.core.default_config import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
CONTENT_TS = SRC / "guide" / "content.ts"
GUIDE_DIR = "docs/guide/"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
APP_GUIDE_LINK_RE = re.compile(r"['\"`](/guide(?:/[^'\"`$\s*]*)?)['\"`]")
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def _glob_patterns() -> list:
    block = re.search(r"import\.meta\.glob<string>\(\s*\[(.*?)\]", CONTENT_TS.read_text(), re.S)
    assert block, "couldn't find the import.meta.glob list in src/guide/content.ts"
    return re.findall(r"'([^']+)'", block.group(1))


def _guide_files() -> list:
    files = set()
    for pattern in _glob_patterns():
        rel = os.path.normpath(os.path.join("src/guide", pattern))
        files.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(rel) if p.is_file())
    return sorted(files)


def _slug_for(file: str) -> str:
    if file.startswith(GUIDE_DIR):
        rel = file[len(GUIDE_DIR):-len(".md")]
        return "" if rel == "index" else re.sub(r"/index$", "", rel)
    return "reference/" + Path(file).stem.lower().replace("_", "-")


def _prose_lines(source: str) -> list:
    lines, in_fence = [], False
    for line in re.sub(r"<!--.*?-->", "", source, flags=re.S).split("\n"):
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _heading_text(raw: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", raw)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\*\*|__|\*", "", text).strip()


def _slugify(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def _headings(file: str) -> list:
    out = []
    for line in _prose_lines((ROOT / file).read_text()):
        match = HEADING_RE.match(line)
        if match:
            out.append(_slugify(_heading_text(match.group(2))))
    return out


def _links(file: str) -> list:
    out = []
    for line in _prose_lines((ROOT / file).read_text()):
        out.extend(LINK_RE.findall(re.sub(r"`[^`]*`", "", line)))
    return out


def _route_patterns() -> list:
    paths = re.findall(r"path=\"([^\"]+)\"", (SRC / "App.tsx").read_text())
    patterns = []
    for path in paths:
        if path == "*":
            continue
        body = re.sub(r":[^/]+", "[^/]+", path.rstrip("*").rstrip("/") or "/")
        patterns.append(re.compile(f"^{body}{'(/.*)?' if path.endswith('*') else ''}/?$"))
    return patterns


GUIDE_FILES = _guide_files()
PAGES = {_slug_for(f): f for f in GUIDE_FILES}
ROUTES = _route_patterns()


def _split(href: str):
    target, _, anchor = href.partition("#")
    return target, anchor


def _guide_link_problem(href: str):
    path, anchor = _split(href)
    slug = re.sub(r"\.md$", "", path[len("/guide"):].strip("/"))
    page = PAGES.get(slug)
    if page is None:
        return f"{href}: no guide page {slug!r}"
    if anchor and anchor not in _headings(page):
        return f"{href}: no heading #{anchor} in {page}"
    return None


def _link_problem(href: str, file: str):
    if SCHEME_RE.match(href):
        return None
    target, anchor = _split(href)
    if not target:
        return None if anchor in _headings(file) else f"#{anchor}: no such heading"
    if target == "/guide" or target.startswith("/guide/"):
        return _guide_link_problem(href)
    if target.startswith("/"):
        return None if any(r.match(target.split("?")[0]) for r in ROUTES) else f"{href}: no app route"
    resolved = os.path.normpath(os.path.join(os.path.dirname(file), target))
    if not (ROOT / resolved).exists():
        return f"{href}: {resolved} doesn't exist"
    if anchor and resolved.endswith(".md") and anchor not in _headings(resolved):
        return f"{href}: no heading #{anchor} in {resolved}"
    return None


def test_guide_has_a_home_page():
    assert "docs/guide/index.md" in GUIDE_FILES
    assert PAGES[""] == "docs/guide/index.md"


@pytest.mark.parametrize("file", GUIDE_FILES)
def test_guide_links_resolve(file):
    problems = [p for href in _links(file) if (p := _link_problem(href, file))]
    assert not problems, f"broken links in {file}:\n" + "\n".join(problems)


@pytest.mark.parametrize("file", GUIDE_FILES)
def test_headings_are_unique_per_page(file):
    seen, dupes = set(), set()
    for slug in _headings(file):
        (dupes if slug in seen else seen).add(slug)
    assert not dupes, f"{file} repeats heading anchors {sorted(dupes)}; links can only reach the first"


def test_app_links_into_the_guide_resolve():
    found, problems = [], []
    for path in SRC.rglob("*.ts*"):
        for href in APP_GUIDE_LINK_RE.findall(path.read_text()):
            found.append(href)
            if problem := _guide_link_problem(href):
                problems.append(f"{path.relative_to(ROOT)}: {problem}")
    assert found, "expected at least one /guide link in src/"
    assert not problems, "\n".join(problems)


def test_nav_sections_name_real_pages():
    block = re.search(r"NAV_SECTIONS: NavSection\[\] = \[(.*?)\n\];", CONTENT_TS.read_text(), re.S)
    assert block, "couldn't find NAV_SECTIONS in src/guide/content.ts"
    slugs = [s for group in re.findall(r"slugs: \[([^\]]*)\]", block.group(1)) for s in re.findall(r"'([^']*)'", group)]
    missing = [s for s in slugs if s not in PAGES]
    assert not missing, f"NAV_SECTIONS lists pages that don't exist: {missing}"


@pytest.mark.parametrize("file", GUIDE_FILES)
def test_guide_does_not_hardcode_the_brand(file):
    default = DEFAULT_CONFIG["branding"]["name"]
    text = (ROOT / file).read_text().lower()
    for name in {default, default.replace("-", " ")}:
        assert name.lower() not in text, f"{file} names {name!r}; write {{{{brand_name}}}} instead"

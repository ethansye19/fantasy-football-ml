"""Text rules shared by the QA compliance scans (QA-4.1, docs/SECURITY.md section 4).

Used by ``tests/compliance/test_qa_source_scans.py`` (static scans of ``api/``
and ``web/``) and ``tests/api/test_qa_response_compliance.py`` (every string
the API returns in private_test). Not a test module.

Rules (owner limits, D-007, D-097 to D-099, PROMPT_1.md):

(b) approval claims: the word "approved" only in negated forms ("not
    approved", "no approved model", "No model is approved").
(c) no "lock", "guaranteed", "can't miss", "sure thing" and no betting
    language. Disclaimers are fine: "No guarantees", "nothing here is betting
    advice". "betting lines" is allowed as a description of model inputs
    (nflverse closing lines, D-008).
(e) original branding: no other fantasy-advice or betting product names in
    user-facing text, and the product named in PROMPT_1.md as the inspiration
    nowhere in api/ or web/.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "api"
WEB_DIR = REPO / "web"
#: web directories whose text reaches users (tests and tooling excluded)
WEB_COPY_DIRS = ("app", "components", "lib", "mock")
SKIP_DIRS = frozenset({"node_modules", ".next", "__pycache__", "out", "playwright-report",
                       "test-results", ".turbo"})
TEXT_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".mts", ".mjs", ".js", ".cjs", ".json", ".css",
                           ".md", ".ini", ".toml", ".yml", ".yaml", ".txt", ".html", ".mako",
                           ".cfg", ".env", ""})

# --------------------------------------------------------------------------- (b) approval claims

_NEGATED_APPROVED = re.compile(
    r"\b(?:not|no|never)[\s-]+(?:yet[\s-]+)?approved\b"
    r"|\bno\s+model\s+(?:is|was|has\s+been)\s+approved\b"
    r"|\b(?:has|have|had)\s+not\s+been\s+approved\b"
    r"|\b(?:isn|wasn|hasn)['’]t\s+(?:been\s+)?approved\b",
    re.IGNORECASE,
)
_APPROVED_WORD = re.compile(r"\bapproved\b", re.IGNORECASE)


def approval_claims(text: str) -> list[str]:
    """Occurrences of the word "approved" that are not negated."""
    rest = _NEGATED_APPROVED.sub(" ", text)
    return [m.group(0) for m in _APPROVED_WORD.finditer(rest)]


# --------------------------------------------------------------------------- (c) banned language

_NEGATION = re.compile(
    r"\b(?:no|not|nothing|never|none|without|neither|nor|cannot)\b"
    r"|\b(?:isn|aren|don|doesn|won)['’]t\b",
    re.IGNORECASE,
)
_SENTENCE_BREAK = re.compile(r"[.!?;:\n]")

#: (name, pattern, negation exempts it)
BANNED: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("lock", re.compile(r"\block(?:s|ed)?\b", re.IGNORECASE), False),
    ("guaranteed", re.compile(r"\bguarantee(?:d)?\b", re.IGNORECASE), True),
    ("can't miss", re.compile(r"\bcan(?:['’]?t|not)[\s-]+miss\b", re.IGNORECASE), False),
    ("sure thing", re.compile(r"\bsure[\s-]+things?\b", re.IGNORECASE), False),
    ("risk-free", re.compile(r"\brisk[\s-]*free\b", re.IGNORECASE), False),
    ("free money", re.compile(r"\bfree[\s-]+money\b", re.IGNORECASE), False),
    (
        "betting",
        re.compile(
            r"\b(?:bet|bets|betting|bettors?|wager\w*|parlay\w*|sportsbooks?|moneylines?"
            r"|bet[\s-]?slips?|best[\s-]+bets?)\b",
            re.IGNORECASE,
        ),
        True,
    ),
)
#: descriptive phrases removed before the betting check (model inputs, D-008)
DESCRIPTIVE = (re.compile(r"\bbetting\s+lines?\b", re.IGNORECASE),)


def _negated(text: str, start: int) -> bool:
    """A negation earlier in the same sentence (``Nothing here is betting advice``)."""
    head = text[:start]
    breaks = list(_SENTENCE_BREAK.finditer(head))
    sentence = head[breaks[-1].end():] if breaks else head
    return bool(_NEGATION.search(sentence))


def banned_language(text: str) -> list[str]:
    """Names of the banned-language rules that ``text`` breaks, with the matched words."""
    for pattern in DESCRIPTIVE:
        text = pattern.sub(" ", text)
    hits: list[str] = []
    for name, pattern, negatable in BANNED:
        for m in pattern.finditer(text):
            if negatable and _negated(text, m.start()):
                continue
            hits.append(f"{name}: {m.group(0)!r}")
    return hits


# --------------------------------------------------------------------------- (e) branding

#: fantasy-advice and betting products (sources and import platforms such as
#: nflverse, Sleeper, FTN, ESPN and Yahoo are not in this list)
OTHER_PRODUCTS = re.compile(
    r"fantasy\s*pros|draft\s*kings|fan\s*duel|prize\s*picks|underdog\s+fantasy|rotowire"
    r"|rotoworld|number\s*fire|\b4for4\b|fantasy\s+footballers|pro\s+football\s+focus|\bPFF\b"
    r"|\bbetmgm\b|caesars\s+sportsbook|sleeper\s+picks|fantasy\s*life\b|footballguys",
    re.IGNORECASE,
)


def inspiration_names() -> re.Pattern[str]:
    """The product PROMPT_1.md says the app is inspired by, as a spacing-tolerant pattern."""
    names = {"WalterPicks"}
    prompt = REPO / "PROMPT_1.md"
    if prompt.is_file():
        m = re.search(r"inspired by\s+([A-Z][\w-]+?)['’]s", prompt.read_text(encoding="utf-8"))
        if m:
            names.add(m.group(1))
    parts = []
    for name in sorted(names):
        words = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name) or [name]
        parts.append(r"[\s_.-]*".join(re.escape(w) for w in words))
    return re.compile("|".join(parts), re.IGNORECASE)


# --------------------------------------------------------------------------- source walking


def walk(root: Path, *, suffixes: frozenset[str] = TEXT_SUFFIXES) -> Iterator[Path]:
    """Text files under ``root``, skipping dependency and build directories."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file() and path.suffix.lower() in suffixes:
            yield path


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def python_user_strings(path: Path) -> Iterator[tuple[int, str]]:
    """``(line, text)`` of every string constant in a Python file except docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.lineno, node.value


@dataclass(frozen=True)
class Segment:
    """A piece of user-visible text in a TS/JS file."""

    line: int
    kind: str  # "string" or "jsx"
    text: str  # without quotes
    before: str  # code before it on the same line (comments and other strings removed)
    after: str  # code after it on the same line


_TS_TOKEN = re.compile(
    r"""(?P<s>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)
      | (?P<bc>/\*.*?\*/)
      | (?P<lc>//[^\n]*)""",
    re.S | re.X,
)
_JSX_INLINE = re.compile(r">([^<>{}]*[A-Za-z][^<>{}]*)<")
_JSX_LINE = re.compile(r"^[A-Za-z’'\"(][^{}<>;=]*[A-Za-z.!?)]$")


def ts_segments(path: Path) -> list[Segment]:
    """String literals (and, in .tsx, JSX text) of a TS/JS file, comments removed."""
    text = path.read_text(encoding="utf-8")
    code_chars: list[str] = []
    strings: list[tuple[int, int, str]] = []  # (start offset in code, end offset, content)
    pos = 0
    length = 0  # length of the code text built so far

    def emit(piece: str) -> None:
        nonlocal length
        code_chars.append(piece)
        length += len(piece)

    for m in _TS_TOKEN.finditer(text):
        emit(text[pos:m.start()])
        if m.group("s") is not None:
            literal = m.group("s")
            strings.append((length, length + 2, literal[1:-1]))
            emit('""' + "\n" * literal.count("\n"))  # multi-line templates keep line numbers
        else:  # comment: keep line breaks so line numbers stay right
            emit("\n" * m.group(0).count("\n"))
        pos = m.end()
    emit(text[pos:])
    code = "".join(code_chars)

    line_starts = [0] + [i + 1 for i, ch in enumerate(code) if ch == "\n"]

    def line_of(offset: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    segments: list[Segment] = []
    for start, end, content in strings:
        line = line_of(start)
        line_start = line_starts[line - 1]
        line_end = code.find("\n", end)
        line_end = len(code) if line_end < 0 else line_end
        segments.append(
            Segment(line, "string", content, code[line_start:start], code[end:line_end])
        )
    if path.suffix == ".tsx":
        for i, raw in enumerate(code.split("\n"), start=1):
            for m in _JSX_INLINE.finditer(raw):
                if m.group(1).strip():
                    segments.append(Segment(i, "jsx", m.group(1).strip(), "", ""))
            stripped = raw.strip()
            if stripped.count(" ") >= 2 and _JSX_LINE.match(stripped):
                segments.append(Segment(i, "jsx", stripped, "", ""))
    return segments


def web_copy_files() -> list[Path]:
    suffixes = frozenset({".ts", ".tsx", ".mts", ".mjs", ".js"})
    out: list[Path] = []
    for name in WEB_COPY_DIRS:
        out.extend(p for p in walk(WEB_DIR / name, suffixes=suffixes) if not p.name.endswith(".d.mts"))
    return out


__all__ = [
    "API_DIR",
    "BANNED",
    "OTHER_PRODUCTS",
    "REPO",
    "WEB_DIR",
    "Segment",
    "approval_claims",
    "banned_language",
    "inspiration_names",
    "python_user_strings",
    "rel",
    "ts_segments",
    "walk",
    "web_copy_files",
]

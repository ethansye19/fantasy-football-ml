"""QA-4.1 compliance scans of the source trees (docs/SECURITY.md section 4).

Static checks over ``api/`` and ``web/`` (the working tree, so uncommitted
frontend work is scanned too):

(b) web copy never claims approval: "approved" appears only negated, or as
    the API status value inside the code path that handles
    ``approval_status == "approved"`` (comparisons, ``case``, ``kind``, type
    unions, ternaries guarded by an ``*approved*`` condition).
(c) no "lock", "guaranteed", "can't miss" or betting language in api/ string
    constants (docstrings excluded) or web copy.
(d) no reads of consensus (ECR) data and no holdout unlock anywhere in api/
    or web/.
(e) original branding: the product PROMPT_1.md names as the inspiration
    appears nowhere in api/ or web/; no other fantasy-advice or betting
    product names in user-facing text.

The runtime counterparts (every string the API actually returns in
private_test, and every file it opens) are in
``tests/api/test_qa_response_compliance.py``.
"""

from __future__ import annotations

import re

import pytest

from tests.compliance.qa_text_rules import (
    API_DIR,
    OTHER_PRODUCTS,
    REPO,
    WEB_DIR,
    approval_claims,
    banned_language,
    inspiration_names,
    python_user_strings,
    rel,
    ts_segments,
    walk,
    web_copy_files,
)

WEB_PRESENT = (WEB_DIR / "package.json").is_file()
needs_web = pytest.mark.skipif(not WEB_PRESENT, reason="no web/ app in this checkout")


def _api_files():
    return list(walk(API_DIR, suffixes=frozenset({".py"})))


def test_scans_cover_a_non_trivial_tree():
    assert len(_api_files()) >= 15
    if WEB_PRESENT:
        assert len(web_copy_files()) >= 20


# --------------------------------------------------------------------------- (b) approval in web copy

#: code just before an exact "approved" literal that marks it as the API value (or as a
#: ``data-*`` attribute, which is markup for tests and styling, not visible copy)
_VALUE_BEFORE = re.compile(
    r"(?:===|!==|==|!=|\bcase|\bkind\s*:|\||\btype\s+\w+\s*=|[\w.]*[Aa]pproved\w*\s*\?"
    r"|\bdata-[\w-]+=)\s*$"
)
_VALUE_AFTER = re.compile(r"^\s*(?:===|!==|==|!=|\|)")
#: the mock API may model the approved response itself (never in app code)
_MOCK_VALUE_BEFORE = re.compile(r"\bapproval_status\s*:\s*$")


def _approval_offenders() -> list[str]:
    offenders: list[str] = []
    for path in web_copy_files():
        is_mock = rel(path).startswith("web/mock/")
        for seg in ts_segments(path):
            if not approval_claims(seg.text):
                continue
            if seg.kind == "string" and seg.text.strip().lower() == "approved":
                if _VALUE_BEFORE.search(seg.before) or _VALUE_AFTER.search(seg.after):
                    continue
                if is_mock and _MOCK_VALUE_BEFORE.search(seg.before):
                    continue
            offenders.append(f"{rel(path)}:{seg.line}: {seg.kind} {seg.text[:100]!r}")
    return offenders


@needs_web
def test_web_copy_never_claims_approval():
    assert _approval_offenders() == []


def test_approval_rule_self_check():
    """The rule accepts negations and value checks and rejects claims."""
    assert approval_claims("No approved model. Projections are off.") == []
    assert approval_claims("Test data: not approved") == []
    assert approval_claims("No model is approved. chain_v1 was not approved on 2025.") == []
    assert approval_claims("x-ffml-approval: not-approved; no_approved_model") == []
    assert approval_claims("Approved model") == ["Approved"]
    assert approval_claims("Model approved by the evaluator") == ["approved"]
    assert _VALUE_BEFORE.search('if (data.approval_status === ')
    assert _VALUE_BEFORE.search("    case ")
    assert _VALUE_BEFORE.search("return apiApproved ? ")
    assert not _VALUE_BEFORE.search("return ")
    assert not _VALUE_BEFORE.search("label: ")
    assert _VALUE_BEFORE.search("<div data-approval=")
    assert not _VALUE_BEFORE.search("<span aria-label=")


# --------------------------------------------------------------------------- (c) banned language


def test_api_strings_have_no_banned_language():
    hits = [
        f"{rel(path)}:{line}: {hit}"
        for path in _api_files()
        for line, text in python_user_strings(path)
        for hit in banned_language(text)
    ]
    assert hits == []


@needs_web
def test_web_copy_has_no_banned_language():
    hits = [
        f"{rel(path)}:{seg.line}: {hit} in {seg.text[:80]!r}"
        for path in web_copy_files()
        for seg in ts_segments(path)
        for hit in banned_language(seg.text)
    ]
    assert hits == []


@pytest.mark.parametrize(
    "text,banned",
    [
        ("Lock of the week", True),
        ("A guaranteed starter", True),
        ("He can't miss this week", True),
        ("Best bet: start him", True),
        ("Place a bet on the over", True),
        ("Add him to your parlay", True),
        ("No guarantees: these are probabilities", False),
        ("Projections are probabilities, not guarantees.", False),
        ("Nothing here is betting advice, and no outcome is promised.", False),
        ("Nothing in this app places bets or presents any outcome as certain.", False),
        ("Historical betting lines are closing lines (D-008).", False),
        ("Results are not guaranteed.", False),
        ("Unlock the league settings", False),
    ],
)
def test_banned_language_rule_self_check(text, banned):
    assert bool(banned_language(text)) is banned, banned_language(text)


# --------------------------------------------------------------------------- (d) restricted data

#: identifiers that read, locate or unlock restricted data (D-007, D-051, D-058)
RESTRICTED_READS = (
    "fact_ecr_weekly",
    "ecr_baseline",
    "ffml.eval.ecr",
    "ffml.data.process.ecr",
    "process.ecr",
    "build_ecr",
    "load_ff_rankings",
    "ff_rankings",
    "fantasypros_id",
    "fpecr",
    "fp_latest_weekly",
    "fantasypros.com",
    "processed/restricted",
    "raw/restricted",
    "RESTRICTED_TABLES",
    "restricted_dir",
    "FFML_HOLDOUT_UNLOCK",
    "HOLDOUT_UNLOCK",
    "holdout_unlock",
    "release_holdout",
    "assert_seasons_allowed",
)


def _restricted_hits(root) -> list[str]:
    hits = []
    for path in walk(root):
        if path.name == "package-lock.json":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        low = text.lower()
        for word in RESTRICTED_READS:
            if word.lower() in low:
                line = low[: low.index(word.lower())].count("\n") + 1
                hits.append(f"{rel(path)}:{line}: {word}")
    return hits


def test_api_never_reads_consensus_data_or_unlocks_the_holdout():
    assert _restricted_hits(API_DIR) == []


@needs_web
def test_web_never_reads_consensus_data_or_unlocks_the_holdout():
    assert _restricted_hits(WEB_DIR) == []


def test_api_image_and_compose_never_unlock_the_holdout():
    for name in ("Dockerfile.api", "Dockerfile.web", "docker-compose.yml"):
        path = REPO / name
        if path.is_file():
            assert "HOLDOUT_UNLOCK" not in path.read_text(encoding="utf-8"), name


# --------------------------------------------------------------------------- (e) branding


def test_inspiration_product_name_appears_nowhere_in_api_or_web():
    pattern = inspiration_names()
    assert pattern.search("walter picks") and pattern.search("WalterPicks")
    hits = []
    for root in (API_DIR, WEB_DIR):
        for path in walk(root):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for m in pattern.finditer(text):
                hits.append(f"{rel(path)}:{text[: m.start()].count(chr(10)) + 1}")
            if pattern.search(path.name):
                hits.append(f"{rel(path)}: file name")
    assert hits == []


def test_api_user_strings_name_no_other_product():
    hits = [
        f"{rel(path)}:{line}: {m.group(0)!r}"
        for path in _api_files()
        for line, text in python_user_strings(path)
        for m in OTHER_PRODUCTS.finditer(text)
    ]
    assert hits == []


@needs_web
def test_web_copy_names_no_other_product():
    hits = [
        f"{rel(path)}:{seg.line}: {m.group(0)!r}"
        for path in web_copy_files()
        for seg in ts_segments(path)
        for m in OTHER_PRODUCTS.finditer(seg.text)
    ]
    package = (WEB_DIR / "package.json").read_text(encoding="utf-8")
    hits += [f"web/package.json: {m.group(0)!r}" for m in OTHER_PRODUCTS.finditer(package)]
    assert hits == []

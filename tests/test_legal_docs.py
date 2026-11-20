"""Legal-docs drift test (L-02).

Rule: the minimal legal set (terms of service, privacy policy, data
consent, content policy, plus the status file) must exist, must keep
its key sections, and must stay in sync with the two code sources
that fix its facts:

- ``puspa/consent/consent.py`` — ``TEKS_VERSI`` (the consent text
  version users actually agree to). ``docs/data-consent.md`` quotes
  that text, so a new consent version without a doc update silently
  leaves users agreeing to words the docs no longer describe.
- ``puspa/billing/config.py`` — ``price_idr_per_month`` per tier and
  ``price_idr`` per credit pack. ``docs/terms-of-service.md`` quotes
  those prices, so a price change without a doc update silently
  advertises a stale price.

Two hard boundaries are also enforced, because the ticket forbids
them outright (see ``docs/legal.md``):

- No licence name may appear in these documents: choosing a licence
  is the owner's decision (D-01, still open) and the repo has no
  ``LICENSE`` file.
- No compliance claim may appear: no specific data-protection law
  compliance is claimed, and the legal judgement belongs to L-01.

This test uses only the standard library and touches no database
(it parses the two code sources with regex instead of importing
them), so it also runs in CI's dependency-free ``checks`` lane with
``--noconftest``, like ``test_docs_drift.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

LEGAL_DOCS = {
    "terms-of-service.md": (
        "What Puspa is",
        "What the agent does, and what it never does",
        "Plans and prices",
        "Your responsibilities",
        "Suspension and termination",
    ),
    "privacy-policy.md": (
        "Who controls your data",
        "Data collected",
        "How your data is used",
        "How your data is protected",
        "Who can see your data",
        "How long data is kept",
        "Your rights",
    ),
    "data-consent.md": (
        "The consent text",
        "How consent is recorded",
        "Withdrawing consent",
    ),
    "content-policy.md": (
        "Prohibited uses",
        "What the agent will not do",
        "How the rules are enforced",
        "Reporting content",
    ),
    "legal.md": (
        "The documents",
        "What is NOT claimed",
        "The legal review still needed",
        "Open decisions",
    ),
}

#: Licence names that must never appear in the legal docs (D-01 open).
FORBIDDEN_LICENCE_NAMES = (
    "Apache-2.0",
    "Apache License",
    "MIT License",
    "GPL",
    "MPL",
    "BSD-3-Clause",
    "BSD License",
)

#: Phrases that would claim a legal compliance the docs must not claim.
FORBIDDEN_COMPLIANCE_CLAIMS = (
    "complies with",
    "is compliant with",
    "fully compliant",
    "memenuhi UU PDP",
)


def _read_doc(name: str) -> str:
    path = DOCS / name
    assert path.is_file(), f"legal doc missing: {path}"
    return path.read_text(encoding="utf-8")


def _consent_version_from_code() -> str:
    """Parse TEKS_VERSI from puspa/consent/consent.py (no import)."""
    source = (REPO_ROOT / "puspa" / "consent" / "consent.py").read_text(encoding="utf-8")
    match = re.search(r'^TEKS_VERSI = "([^"]+)"', source, flags=re.MULTILINE)
    assert match is not None, "could not parse TEKS_VERSI from consent.py"
    return match.group(1)


def _idr_prices_from_billing_config() -> list[int]:
    """Parse every price_idr_per_month / price_idr from billing config."""
    source = (REPO_ROOT / "puspa" / "billing" / "config.py").read_text(encoding="utf-8")
    prices = re.findall(r"price_idr(?:_per_month)?=(\d[\d_]*)", source)
    assert prices, "no prices parsed from puspa/billing/config.py"
    return [int(value.replace("_", "")) for value in prices]


def test_legal_docs_exist_with_key_sections() -> None:
    """Each legal doc exists and keeps its key sections."""
    for name, sections in LEGAL_DOCS.items():
        text = _read_doc(name)
        for section in sections:
            assert section in text, (
                f"docs/{name} lost its {section!r} section — "
                "restore it or update this test with the rename"
            )


def test_consent_version_matches_code() -> None:
    """data-consent.md quotes the consent version the code records."""
    version = _consent_version_from_code()
    text = _read_doc("data-consent.md")
    assert version in text, (
        f"docs/data-consent.md does not mention consent version "
        f"{version!r} from puspa/consent/consent.py — update the doc "
        "when the in-product consent text changes"
    )
    assert "Puspa data consent (version" in text, (
        "docs/data-consent.md lost the verbatim quote of the in-product consent text"
    )


def test_terms_prices_match_billing_config() -> None:
    """terms-of-service.md quotes the prices in billing config."""
    text = _read_doc("terms-of-service.md")
    for price in _idr_prices_from_billing_config():
        shown = f"{price:,}"
        assert shown in text, (
            f"docs/terms-of-service.md does not quote Rp{shown}, "
            "but puspa/billing/config.py charges it — update the doc "
            "when a price changes"
        )


def test_no_licence_name_in_legal_docs() -> None:
    """No licence is named: choosing one is the owner's open D-01."""
    for name in LEGAL_DOCS:
        text = _read_doc(name)
        for licence in FORBIDDEN_LICENCE_NAMES:
            assert licence not in text, (
                f"docs/{name} names {licence!r} — the project licence "
                "is undecided (D-01) and no licence may be named here"
            )


def test_no_compliance_claim_in_legal_docs() -> None:
    """No data-protection compliance is claimed: that is L-01's call."""
    for name in LEGAL_DOCS:
        text = _read_doc(name)
        for claim in FORBIDDEN_COMPLIANCE_CLAIMS:
            assert claim not in text, (
                f"docs/{name} claims {claim!r} — no specific "
                "data-protection compliance may be claimed before L-01"
            )

"""i18n catalog audit (Nero, 24 Nov): every UI string translated.

The server merges a missing key from Indonesian, so a key that
exists only in ``id.json`` still leaks Indonesian into the
English UI. This test fails the build on exactly that:

- the id and en catalogs carry the same key set;
- every ``t("ui.…")`` key used anywhere in the PWA source exists
  in both catalogs (a screen that invents a key without adding
  its translations fails here, not silently in English);
- every ``ui.*`` value is non-empty in both languages.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "apps" / "pwa" / "src"
LOCALES = ROOT / "puspa" / "i18n" / "locales"

KEY_RE = re.compile(r"""t\(\s*["'](ui\.[A-Za-z0-9_.]+)["']""")


def _load(locale: str) -> dict:
    return json.loads((LOCALES / f"{locale}.json").read_text())


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for path in SRC.rglob("*.ts*"):
        keys |= set(KEY_RE.findall(path.read_text()))
    return keys


def test_catalogs_have_the_same_keys():
    id_keys = set(_load("id"))
    en_keys = set(_load("en"))
    assert id_keys == en_keys, (
        "id-only: " + str(sorted(id_keys - en_keys)) + "; "
        "en-only: " + str(sorted(en_keys - id_keys))
    )


def test_every_used_key_exists_in_both_catalogs():
    id_cat, en_cat = _load("id"), _load("en")
    used = _used_keys()
    assert used, "no t() keys found — the audit regex is broken"
    missing_id = sorted(k for k in used if k not in id_cat)
    missing_en = sorted(k for k in used if k not in en_cat)
    assert not missing_id, f"keys missing from id.json: {missing_id}"
    assert not missing_en, f"keys missing from en.json: {missing_en}"


def test_ui_values_are_non_empty():
    for locale in ("id", "en"):
        catalog = _load(locale)
        empty = sorted(k for k, v in catalog.items() if k.startswith("ui.") and not str(v).strip())
        assert not empty, f"empty ui.* values in {locale}.json: {empty}"

#!/usr/bin/env python3
"""Rename all Indonesian identifiers/values to English in the repo.

Uses rename_map.json (built by build_rename_map.py) as the single source
of truth. Run from the repo root: python3 rename.py [--dry-run]

Stages (in order):
  1. special_cases (file-scoped exact replacements)
  2. module_paths (dotted import paths, plain text)
  3. file renames (files map + cassette JSON basenames)
  4. .py files (tokenize + position-based splicing; docstrings skipped)
  5. .sql files (SQL handler)
  6. .json files (cassettes: recursive; blind-test: keys only)
  7. PWA templates (Jinja blocks)
  8. sw.js (targeted route rename)

Design notes (lessons from the first pass):
- .py files use POSITION-BASED SPLICING (token offsets), never
  tokenize.untokenize (it mangles formatting).
- rename_identifier never returns a Python keyword or builtin name.
- SQL table context covers CREATE/DROP TABLE IF [NOT] EXISTS,
  CREATE INDEX ... ON t (...), GRANT/REVOKE ... ON t TO/FROM,
  CREATE/DROP POLICY ... ON t, and comma-separated table lists.
- Singular table names created by an earlier bad pass are fixed in
  table position via SING2PLUR (e.g. event -> events, summary ->
  summaries, user -> users).
- SQL fragments (ORDER BY / WHERE / VALUES / RETURNING / SET / FROM t)
  are detected so split string literals are renamed too.
"""
import io
import json
import keyword
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAP = json.load(open(ROOT / "rename_map.json"))
IDENT = MAP["identifiers"]
COMP = MAP["components"]
VALUES = MAP["values"]
TABLES = MAP["tables"]
COLUMNS = MAP["columns"]
POLICIES = MAP["policies"]
MODPATHS = MAP["module_paths"]
FILES = MAP["files"]
DRY = "--dry-run" in sys.argv

# Table names created in the singular (from IDENT/COLUMNS/COMP) while the
# canonical name is the plural (from TABLES). In table position, plural wins.
SING2PLUR = {}
for _t, _plural in TABLES.items():
    for _src in (IDENT, COLUMNS, COMP):
        _sing = _src.get(_t)
        if _sing and _sing != _plural:
            SING2PLUR[_sing] = _plural

SNAKE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]*")
SQL_KW = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE)\b",
    re.IGNORECASE)
SQL_FRAGMENT_RE = re.compile(
    r"\b(ORDER\s+BY|GROUP\s+BY|WHERE\b|VALUES\s*\(|RETURNING\b|LIMIT\b|"
    r"OFFSET\b|JOIN\b|ON\s+DELETE|ON\s+CONFLICT|HAVING\b|DISTINCT\b|"
    r"EXCLUDED\.|ON\s+CONFLICT\b)",
    re.IGNORECASE)
SCHEMA_WORDS = set(TABLES) | set(COLUMNS) | set(SING2PLUR) | set(VALUES)
TABLE_CTX = {"FROM", "JOIN", "INTO", "UPDATE", "TABLE", "TRUNCATE", "REFERENCES"}
BUILTINS = set(dir(__builtins__))

stats = {"files_changed": 0, "names": 0, "strings": 0, "sql_words": 0}


def camel_parts(name):
    return re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", name)


def rename_identifier(name):
    """Rename one identifier. Never returns a keyword or builtin name."""
    result = _rename_identifier_inner(name)
    if result[0] != name and (
            keyword.iskeyword(result[0]) or result[0] in BUILTINS):
        return name, False
    return result


def _rename_identifier_inner(name):
    if name in IDENT:
        return IDENT[name], True
    if name in COLUMNS:
        return COLUMNS[name], True
    if keyword.iskeyword(name):
        return name, False
    if name.isupper() and "_" in name:
        parts = name.split("_")
        out, changed = [], False
        for p in parts:
            low = p.lower()
            if low in COMP:
                out.append(COMP[low].upper())
                changed = True
            else:
                out.append(p)
        return ("_".join(out), changed) if changed else (name, False)
    if re.search(r"[A-Z]", name) and not name.isupper():
        parts = camel_parts(name)
        if len(parts) > 1:
            out, changed = [], False
            for p in parts:
                low = p.lower()
                if low in COMP:
                    new = COMP[low]
                    out.append(new[:1].upper() + new[1:] if p[:1].isupper() else new)
                    changed = True
                else:
                    out.append(p)
            return ("".join(out), changed) if changed else (name, False)
    if "_" in name:
        parts = name.split("_")
        out, changed = [], False
        for p in parts:
            if p in COMP:
                out.append(COMP[p])
                changed = True
            else:
                out.append(p)
        return ("_".join(out), changed) if changed else (name, False)
    if name in COMP:
        return COMP[name], True
    return name, False


def rename_snake_tokens(text):
    def _sub(m):
        tok = m.group(0)
        new, changed = rename_identifier(tok)
        if changed:
            stats["strings"] += 1
        return new if changed else tok
    return SNAKE_RE.sub(_sub, text)


def rename_words_in_text(text):
    def _sub(m):
        tok = m.group(0)
        new, changed = rename_identifier(tok)
        return new if changed else tok
    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", _sub, text)


# ---------------------------------------------------------------- SQL ---

def rename_sql(text):
    out = []
    i, n = 0, len(text)
    prev_words = []
    in_table_list = False

    def _is_table_name(word):
        if in_table_list:
            return word in TABLES or word in SING2PLUR
        if prev_words and prev_words[-1] in TABLE_CTX:
            return True
        if len(prev_words) >= 4 and prev_words[-4:] == [
                "TABLE", "IF", "NOT", "EXISTS"]:
            return True
        if len(prev_words) >= 3 and prev_words[-3:] == [
                "TABLE", "IF", "EXISTS"]:
            return True
        if prev_words and prev_words[-1] == "ON":
            if word not in TABLES and word not in SING2PLUR:
                return False
            rest = text[i + len(word):].lstrip()
            if rest.startswith("(") or rest.startswith(";") or rest == "":
                return True
            nxt = re.match(r"[A-Za-z_][A-Za-z0-9_]*", rest)
            if nxt and nxt.group(0).upper() in (
                    "TO", "FROM", "FOR", "USING", "AS"):
                return True
            return False
        return False

    while i < n:
        ch = text[i]
        if text.startswith("$$", i):
            j = text.find("$$", i + 2)
            if j == -1:
                out.append(text[i:])
                break
            inner = rename_sql(text[i + 2:j])
            out.append("$$" + inner + "$$")
            i = j + 2
            prev_words = []
            in_table_list = False
            continue
        if ch == "'":
            j = i + 1
            buf = []
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        buf.append("''")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            lit = "".join(buf)
            new = TABLES.get(lit, VALUES.get(lit, POLICIES.get(lit, lit)))
            if new != lit:
                stats["sql_words"] += 1
            out.append("'" + new + "'")
            i = j + 1
            prev_words = []
            in_table_list = False
            continue
        if ch == '"':
            j = text.find('"', i + 1)
            if j == -1:
                out.append(text[i:])
                break
            lit = text[i + 1:j]
            new = COLUMNS.get(lit, TABLES.get(lit, lit))
            if new != lit:
                stats["sql_words"] += 1
            out.append('"' + new + '"')
            i = j + 1
            prev_words = []
            in_table_list = False
            continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i:])
        if m:
            word = m.group(0)
            is_table = _is_table_name(word)
            if is_table:
                new = SING2PLUR.get(word, TABLES.get(word, word))
                rest = text[i + len(word):].lstrip()
                in_table_list = rest.startswith(",")
            else:
                new = COLUMNS.get(word, POLICIES.get(word, word))
                if new == word:
                    new, _ = rename_identifier(word)
                in_table_list = False
            if new != word:
                stats["sql_words"] += 1
            out.append(new)
            prev_words = (prev_words + [word.upper()])[-4:]
            i += len(word)
            continue
        out.append(ch)
        if not ch.isspace():
            prev_words = []
            if ch != ",":
                in_table_list = False
        i += 1
    return "".join(out)


def _looks_like_sql(content):
    """True if a string literal looks like SQL (not prose)."""
    if SQL_FRAGMENT_RE.search(content):
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", content))
        return bool(words & SCHEMA_WORDS)
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", content))
    stripped = content.strip()
    if stripped.startswith("(") and words & set(COLUMNS):
        return True
    if "," in content:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_(),\s%]*", stripped):
            if words & set(COLUMNS):
                return True
    if re.search(r"\bVALUES\s*\(", content, re.IGNORECASE):
        return bool(words & set(VALUES))
    if re.search(r"\b(AND|OR)\s+[A-Za-z_][A-Za-z0-9_]*\s*(=|IN\b|IS\b|LIKE\b)",
                 content, re.IGNORECASE):
        return bool(words & set(COLUMNS))
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*%s", content):
        return bool(words & set(COLUMNS))
    if re.search(r"\bSET\s+[A-Za-z_][A-Za-z0-9_]*\s*=", content, re.IGNORECASE):
        return bool(words & set(COLUMNS))
    m_from = re.search(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", content, re.IGNORECASE)
    if m_from and m_from.group(1) in set(TABLES) | set(SING2PLUR):
        return True
    return False


def string_new_content(content, is_sql_hint=False):
    stripped = content.strip()
    if stripped in VALUES:
        return VALUES[stripped]
    # A table name used as a plain string literal (e.g. delete_account).
    if stripped in TABLES:
        return TABLES[stripped]
    if stripped in SING2PLUR:
        return SING2PLUR[stripped]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
        if stripped in COLUMNS:
            return COLUMNS[stripped]
        if stripped in IDENT:
            return IDENT[stripped]
        if stripped in COMP:
            return COMP[stripped]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(,[A-Za-z_][A-Za-z0-9_]*)+", stripped):
        return ",".join(rename_identifier(w)[0] for w in stripped.split(","))
    if is_sql_hint or SQL_KW.search(content) or _looks_like_sql(content):
        return rename_sql(content)
    new = content
    for old, rep in MODPATHS.items():
        if old in new:
            new = new.replace(old, rep)
    new = rename_snake_tokens(new)
    return new


def transform_string_token(tok_str):
    m = re.match(r"([rubf]*)('''|\"\"\"|'|\")", tok_str, re.IGNORECASE)
    if not m:
        return tok_str
    prefix, quote = m.group(1), m.group(2)
    inner = tok_str[len(prefix) + len(quote):-len(quote)]
    if "f" in prefix.lower():
        return prefix + quote + rename_words_in_text(inner) + quote
    return prefix + quote + string_new_content(inner) + quote


# ------------------------------------------------------------------ .py ---

def _line_offsets(src):
    offs = [0]
    for line in src.splitlines(keepends=True):
        offs.append(offs[-1] + len(line))
    return offs


def process_py(path):
    src = path.read_text(encoding="utf-8")
    orig_src = src
    for old, rep in MODPATHS.items():
        src = src.replace(old, rep)
    # relative imports of renamed modules
    rel_fixes = [
        ("from .sessions import", "from .user_sessions import"),
        ("from .session import", "from .agent_session import"),
        ("from .skills import", None),  # handled per-package below
        ("from .tagihan import", "from .bills import"),
    ]
    for old, new in rel_fixes:
        if new and old in src:
            src = src.replace(old, new)
    if "from .skills import" in src:
        if "policy" in str(path):
            src = src.replace("from .skills import", "from .skill_signing import")
        else:
            src = src.replace("from .skills import", "from .skill_loader import")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        print(f"WARN tokenize failed: {path}")
        return
    offs = _line_offsets(src)

    def to_off(pos):
        row, col = pos
        return offs[row - 1] + col

    sig = [t for t in tokens
           if t.type not in (tokenize.NL, tokenize.COMMENT, tokenize.ENCODING)]
    repls = []
    for tok in tokens:
        if tok.type == tokenize.NAME:
            new, ch = rename_identifier(tok.string)
            if ch:
                repls.append((to_off(tok.start), to_off(tok.end), new))
                stats["names"] += 1
        elif tok.type == tokenize.STRING:
            is_doc = False
            try:
                si = sig.index(tok)
            except ValueError:
                si = -1
            if si >= 0:
                prev = sig[si - 1] if si > 0 else None
                nxt = sig[si + 1] if si + 1 < len(sig) else None
                prev_ok = (prev is None or prev.type in (
                    tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
                    or prev.string == ":")
                nxt_ok = (nxt is None or nxt.type in (
                    tokenize.NEWLINE, tokenize.DEDENT, tokenize.ENDMARKER))
                if prev_ok and nxt_ok:
                    is_doc = True
            if is_doc:
                continue
            new_tok = transform_string_token(tok.string)
            if new_tok != tok.string:
                repls.append((to_off(tok.start), to_off(tok.end), new_tok))
                stats["strings"] += 1
    if repls or src != orig_src:
        new_src = src
        for start, end, new_text in sorted(repls, reverse=True):
            new_src = new_src[:start] + new_text + new_src[end:]
        if not DRY:
            path.write_text(new_src, encoding="utf-8")
        stats["files_changed"] += 1


# ----------------------------------------------------------------- .sql ---

def process_sql(path):
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    changed = False
    for line in lines:
        if line.lstrip().startswith("--"):
            out.append(line)
            continue
        new = rename_sql(line)
        if new != line:
            changed = True
        out.append(new)
    if changed and not DRY:
        path.write_text("".join(out), encoding="utf-8")
        stats["files_changed"] += 1
    elif changed:
        stats["files_changed"] += 1


# ---------------------------------------------------------------- .json ---

def _rename_json_value(obj, keys_only=False):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            new_k, _ = rename_identifier(k) if isinstance(k, str) else (k, False)
            out[new_k] = v if keys_only else _rename_json_value(v)
        return out
    if isinstance(obj, list):
        return [_rename_json_value(x) for x in obj]
    if isinstance(obj, str) and not keys_only:
        return string_new_content(obj)
    return obj


def process_json(path):
    import json as _json
    data = _json.loads(path.read_text(encoding="utf-8"))
    keys_only = "blind-test" in str(path)
    new = _rename_json_value(data, keys_only=keys_only)
    if new != data and not DRY:
        path.write_text(_json.dumps(new, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        stats["files_changed"] += 1
    elif new != data:
        stats["files_changed"] += 1


# -------------------------------------------------------------- templates ---

def process_template(path):
    text = path.read_text(encoding="utf-8")
    orig = text

    def _repl_block(m):
        inner = m.group(1)
        # Jinja {{ ... }} / {% ... %}: rename identifiers + quoted values
        def _sub_word(w):
            new, ch = rename_identifier(w.group(0))
            return new if ch else w.group(0)
        inner = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", _sub_word, inner)
        return m.group(0)[:2] + inner + m.group(0)[-2:]

    text = re.sub(r"\{\{(.*?)\}\}", _repl_block, text, flags=re.DOTALL)
    text = re.sub(r"\{%(.*?)%\}", _repl_block, text, flags=re.DOTALL)
    if text != orig and not DRY:
        path.write_text(text, encoding="utf-8")
        stats["files_changed"] += 1
    elif text != orig:
        stats["files_changed"] += 1


# ------------------------------------------------------------------ main ---

def main():
    # 1. special cases (file-scoped)
    for sc in MAP["special_cases"]:
        p = ROOT / sc["file"]
        if p.exists():
            t = p.read_text(encoding="utf-8")
            if sc["old"] in t:
                if not DRY:
                    p.write_text(t.replace(sc["old"], sc["new"]), encoding="utf-8")
                stats["files_changed"] += 1

    # 2. module paths are applied inside process_py / string handling.

    # 3. file renames
    for old_rel, new_rel in FILES.items():
        old, new = ROOT / old_rel, ROOT / new_rel
        if old.exists() and not new.exists():
            if not DRY:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
            print(f"renamed file {old_rel} -> {new_rel}")
    # cassette JSON basenames
    cas_dir = ROOT / "cassettes"
    if cas_dir.exists():
        for f in list(cas_dir.glob("*.json")):
            new_name, ch = rename_identifier(f.stem)
            if ch and new_name != f.stem:
                if not DRY:
                    f.rename(cas_dir / (new_name + ".json"))
                print(f"renamed cassette {f.name} -> {new_name}.json")

    # 4-8. per-file processing
    for p in sorted(ROOT.rglob("*")):
        if ".git" in p.parts or ".venv" in p.parts or "__pycache__" in p.parts:
            continue
        if not p.is_file():
            continue
        rel = str(p.relative_to(ROOT))
        if rel in ("rename_map.json", "build_rename_map.py", "rename.py"):
            continue
        if p.suffix == ".py":
            process_py(p)
        elif p.suffix == ".sql":
            process_sql(p)
        elif p.suffix == ".json" and ("cassettes" in p.parts
                                      or "blind-test" in str(p)):
            process_json(p)
        elif p.suffix == ".html" and "apps/pwa/templates" in rel:
            process_template(p)
    # sw.js route
    sw = ROOT / "apps/pwa/static/sw.js"
    if sw.exists():
        t = sw.read_text(encoding="utf-8")
        if "/kantor" in t:
            if not DRY:
                sw.write_text(t.replace("/kantor", "/office"), encoding="utf-8")
            print("sw.js: /kantor -> /office")
            stats["files_changed"] += 1

    print(f"STATS: {stats}")


if __name__ == "__main__":
    main()

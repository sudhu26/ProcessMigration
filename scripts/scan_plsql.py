#!/usr/bin/env python3
"""Scan Oracle SQL / PL/SQL sources for object definitions and references.
Stdlib only.

Usage: scan_plsql.py --root <repo_root> --repo <repo_name> --exts ".sql,.pks,.pkb"
                      --out <out.json>
"""
import argparse, json, os, re

DEF_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+|EDITIONABLE\s+|NONEDITIONABLE\s+)?'
    r'(TABLE|VIEW|MATERIALIZED\s+VIEW|PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|'
    r'TRIGGER|SEQUENCE|SYNONYM|TYPE|INDEX)\s+'
    r'(?:IF\s+NOT\s+EXISTS\s+)?"?([A-Za-z_][\w$]*"?\.)?"?([A-Za-z_][\w$]*)"?',
    re.IGNORECASE)

REF_PATTERNS = [
    (re.compile(r'\bFROM\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'read'),
    (re.compile(r'\bJOIN\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'read'),
    (re.compile(r'\bINSERT\s+INTO\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'write'),
    (re.compile(r'\bUPDATE\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)\s+SET', re.IGNORECASE), 'write'),
    (re.compile(r'\bMERGE\s+INTO\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'write'),
    (re.compile(r'\bTABLE\s*\(\s*([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'read'),
]
CALL_RE = re.compile(r'\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\s*\(')
EXEC_RE = re.compile(r'^\s*(?:EXEC|EXECUTE)\s+([A-Za-z_][\w$.]+)', re.IGNORECASE | re.MULTILINE)

RISKY = {
    'execute_immediate': re.compile(r'\bEXECUTE\s+IMMEDIATE\b', re.IGNORECASE),
    'dbms': re.compile(r'\bDBMS_[\w$]+\b', re.IGNORECASE),
    'utl': re.compile(r'\bUTL_[\w$]+\b', re.IGNORECASE),
    'autonomous_txn': re.compile(r'AUTONOMOUS_TRANSACTION', re.IGNORECASE),
    'bulk_collect': re.compile(r'\bBULK\s+COLLECT\b', re.IGNORECASE),
    'forall': re.compile(r'\bFORALL\b', re.IGNORECASE),
    'goto': re.compile(r'\bGOTO\b', re.IGNORECASE),
}

LINE_COMMENT = re.compile(r'--.*?$', re.MULTILINE)
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
STRING_RE = re.compile(r"'(?:''|[^'])*'")


def analyze_file(path, repo):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return None
    code = BLOCK_COMMENT.sub('', LINE_COMMENT.sub('', text))
    code_nolit = STRING_RE.sub("''", code)

    defs = []
    for m in DEF_RE.finditer(code_nolit):
        kind = re.sub(r'\s+', ' ', m.group(1)).upper()
        schema = (m.group(2) or '').strip('".')
        name = m.group(3).strip('"')
        full = f'{schema}.{name}'.upper() if schema else name.upper()
        defs.append({'kind': kind, 'name': full})

    reads, writes = set(), set()
    for pat, kind in REF_PATTERNS:
        for m in pat.finditer(code_nolit):
            (writes if kind == 'write' else reads).add(m.group(1).upper())
    refs = reads | writes
    calls = set()
    for m in CALL_RE.finditer(code_nolit):
        calls.add(f'{m.group(1)}.{m.group(2)}'.upper())
    calls.discard('EXECUTE.IMMEDIATE')
    calls = {c for c in calls if not c.endswith('.IMMEDIATE')}
    for m in EXEC_RE.finditer(code_nolit):
        name = m.group(1).upper()
        if name != 'IMMEDIATE':  # EXECUTE IMMEDIATE tracked in risky_constructs
            calls.add(name)

    risky = {k: len(p.findall(code_nolit)) for k, p in RISKY.items()}
    risky = {k: v for k, v in risky.items() if v}

    loc = sum(1 for ln in code.splitlines() if ln.strip())
    return {
        'path': path, 'repo': repo,
        'definitions': defs, 'references': sorted(refs),
        'reads': sorted(reads), 'writes': sorted(writes),
        'calls': sorted(calls),
        'risky_constructs': risky,
        'metrics': {'loc': loc, 'def_count': len(defs)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--exts', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    exts = tuple(e.lower() for e in a.exts.split(','))
    records = []
    for dirpath, _, filenames in os.walk(a.root):
        for fn in filenames:
            if fn.lower().endswith(exts):
                rec = analyze_file(os.path.join(dirpath, fn), a.repo)
                if rec:
                    records.append(rec)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=1)
    print(f'scanned {len(records)} plsql files -> {a.out}')


if __name__ == '__main__':
    main()

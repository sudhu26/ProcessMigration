#!/usr/bin/env python3
"""Scan Java sources for JDBC usage, embedded SQL, stored-proc calls,
Oracle-dialect features, and inter-file dependencies. Stdlib only.

Usage: scan_java.py --root <repo_root> --repo <repo_name> --out <out.json>
"""
import argparse, json, os, re, sys

SQL_KEYWORDS = re.compile(
    r'\b(SELECT|INSERT\s+INTO|INSERT|UPDATE|DELETE\s+FROM|DELETE|MERGE\s+INTO|MERGE|'
    r'WITH|TRUNCATE|CALL|CREATE|DROP|ALTER)\b', re.IGNORECASE)

TABLE_PATTERNS = [
    (re.compile(r'\bFROM\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'read'),
    (re.compile(r'\bJOIN\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'read'),
    (re.compile(r'\bINTO\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'write'),
    (re.compile(r'\bUPDATE\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)\s+SET', re.IGNORECASE), 'write'),
    (re.compile(r'\bMERGE\s+INTO\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'write'),
    (re.compile(r'\bTABLE\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)', re.IGNORECASE), 'read'),
]

PROC_CALL = re.compile(r'\{\s*call\s+([A-Za-z_][\w$.]*)\s*\(?', re.IGNORECASE)
PREPCALL = re.compile(r'prepareCall\s*\(\s*"([^"]+)"', re.IGNORECASE)

JDBC_SIGNALS = {
    'getConnection': re.compile(r'\bgetConnection\s*\('),
    'prepareStatement': re.compile(r'\bprepareStatement\s*\('),
    'prepareCall': re.compile(r'\bprepareCall\s*\('),
    'createStatement': re.compile(r'\bcreateStatement\s*\('),
    'executeQuery': re.compile(r'\bexecuteQuery\s*\('),
    'executeUpdate': re.compile(r'\bexecuteUpdate\s*\('),
    'executeBatch': re.compile(r'\bexecuteBatch\s*\('),
    'addBatch': re.compile(r'\baddBatch\s*\('),
    'setAutoCommit': re.compile(r'\bsetAutoCommit\s*\('),
    'commit': re.compile(r'\bcommit\s*\(\s*\)'),
    'rollback': re.compile(r'\brollback\s*\('),
    'setSavepoint': re.compile(r'\b(SetSavepoint|setSavepoint)\s*\('),
}

ORACLE_ISMS = {
    'ROWNUM': re.compile(r'\bROWNUM\b', re.IGNORECASE),
    'SYSDATE_SYSTIMESTAMP': re.compile(r'\b(SYSDATE|SYSTIMESTAMP|CURRENT_DATE)\b', re.IGNORECASE),
    'DUAL': re.compile(r'\bFROM\s+DUAL\b', re.IGNORECASE),
    'NVL': re.compile(r'\bNVL[2]?\s*\(', re.IGNORECASE),
    'DECODE': re.compile(r'\bDECODE\s*\(', re.IGNORECASE),
    'OLD_JOIN_OP': re.compile(r'\(\+\)'),
    'CONNECT_BY': re.compile(r'\b(CONNECT\s+BY|START\s+WITH)\b', re.IGNORECASE),
    'SEQUENCE': re.compile(r'\bNEXTVAL\b|\bCURRVAL\b', re.IGNORECASE),
    'FOR_UPDATE': re.compile(r'\bFOR\s+UPDATE\b', re.IGNORECASE),
    'HINT': re.compile(r'/\*\+'),
    'MODEL_CLAUSE': re.compile(r'\bMODEL\b', re.IGNORECASE),
    'PIVOT': re.compile(r'\bPIVOT\b', re.IGNORECASE),
}

STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"')
TEXTBLOCK_RE = re.compile(r'"""(.*?)"""', re.DOTALL)
LINE_COMMENT = re.compile(r'//.*?$', re.MULTILINE)
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
PACKAGE_RE = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.MULTILINE)
IMPORT_RE = re.compile(r'^\s*import\s+(static\s+)?([\w.*]+)\s*;', re.MULTILINE)
CLASS_RE = re.compile(r'\b(public\s+|protected\s+|private\s+)?(class|interface|enum|record)\s+(\w+)')
METHOD_RE = re.compile(r'\b(public|protected|private|static|\s)+\s*[\w<>\[\]]+\s+(\w+)\s*\([^;{}]*\)\s*(throws\s+[\w,\s]+)?\{')
BRANCH_RE = re.compile(r'\b(if|for|while|switch|catch|case)\b')


def strip_comments(text):
    text = BLOCK_COMMENT.sub('', text)
    text = LINE_COMMENT.sub('', text)
    return text


def extract_literals(text):
    """Extract string literals, merging ones concatenated with + (the usual
    Java SQL-building pattern), replacing injected variables with ?."""
    spans = []
    for m in TEXTBLOCK_RE.finditer(text):
        spans.append((m.start(), m.end(), m.group(1)))
    tmp = TEXTBLOCK_RE.sub('""', text)
    for m in STRING_RE.finditer(tmp):
        raw = m.group(0)[1:-1]
        try:
            val = bytes(raw, 'utf-8').decode('unicode_escape')
        except Exception:
            val = raw
        spans.append((m.start(), m.end(), val))
    spans.sort()
    between = re.compile(r'^\s*\+\s*(?:[A-Za-z_][\w$.]*(?:\([^)]*\))?\s*\+\s*)?$')
    merged, cur = [], None
    for s, e, val in spans:
        if cur and between.match(text[cur[1]:s]):
            cur = (cur[0], e, cur[2] + ' ? ' + val)
        else:
            if cur:
                merged.append(cur[2])
            cur = (s, e, val)
    if cur:
        merged.append(cur[2])
    return merged


def analyze_file(path, repo):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return None
    literals = extract_literals(text)
    code = strip_comments(TEXTBLOCK_RE.sub('""', STRING_RE.sub('""', text)))

    pkg = PACKAGE_RE.search(code)
    package = pkg.group(1) if pkg else ''
    imports = [m.group(2) for m in IMPORT_RE.finditer(code)]
    classes = [m.group(3) for m in CLASS_RE.finditer(code)]

    jdbc = {name: len(pat.findall(code)) for name, pat in JDBC_SIGNALS.items()}

    sql_lits = [l for l in literals if SQL_KEYWORDS.search(l)]
    reads, writes, procs = set(), set(), set()
    for lit in sql_lits:
        one = ' '.join(lit.split())
        for pat, kind in TABLE_PATTERNS:
            for m in pat.finditer(one):
                name = m.group(1).upper()
                if name in ('DUAL',):
                    continue
                (writes if kind == 'write' else reads).add(name)
        for m in PROC_CALL.finditer(one):
            procs.add(m.group(1).upper())
    for m in PREPCALL.finditer(code):
        procs.add(re.sub(r'[\s{(]', '', m.group(1)).upper().rstrip(')'))

    isms = {name: len(pat.findall(' '.join(sql_lits))) for name, pat in ORACLE_ISMS.items()}
    isms = {k: v for k, v in isms.items() if v}

    loc = sum(1 for ln in code.splitlines() if ln.strip())
    branches = len(BRANCH_RE.findall(code))
    methods = len(METHOD_RE.findall(code))
    has_main = bool(re.search(r'public\s+static\s+void\s+main\s*\(', code))

    return {
        'path': path, 'repo': repo, 'package': package,
        'classes': classes, 'imports': imports,
        'jdbc': {k: v for k, v in jdbc.items() if v},
        'sql_literal_count': len(sql_lits),
        'sql_sample': [' '.join(l.split())[:300] for l in sql_lits[:10]],
        'tables_read': sorted(reads), 'tables_written': sorted(writes),
        'procs_called': sorted(procs),
        'oracle_isms': isms,
        'metrics': {'loc': loc, 'branches': branches,
                    'methods_rough': methods, 'has_main': has_main},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    records = []
    for dirpath, _, filenames in os.walk(a.root):
        for fn in filenames:
            if fn.endswith('.java'):
                rec = analyze_file(os.path.join(dirpath, fn), a.repo)
                if rec:
                    records.append(rec)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=1)
    print(f'scanned {len(records)} java files -> {a.out}')


if __name__ == '__main__':
    main()

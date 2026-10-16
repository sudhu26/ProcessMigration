#!/usr/bin/env python3
"""Deterministic DB-heavy / Java-heavy pre-classification of process units.
Stdlib only. Thresholds come from config.yaml.

Usage: classify.py --units <units.json> --db-heavy-ratio 1.6
                   --java-heavy-ratio 0.6 --min-evidence 3
"""
import argparse, json


def score(u):
    m = u['metrics']
    n_db_objects = len(u['db_objects'])
    db = (1.5 * m['sql_literals'] + 4.0 * m['proc_calls']
          + 1.0 * n_db_objects + 0.5 * m['oracle_ism_total']
          + 1.0 * m['writes'])
    java = (m['loc'] / 40.0 + m['branches'] / 8.0 + m['methods'] / 3.0
            + 0.5 * m['jdbc_sites'])
    evidence = (m['sql_literals'] + m['proc_calls'] + n_db_objects
                + m['jdbc_sites'] + m['loc'] // 100)
    return db, java, evidence


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--units', required=True)
    ap.add_argument('--db-heavy-ratio', type=float, default=1.6)
    ap.add_argument('--java-heavy-ratio', type=float, default=0.6)
    ap.add_argument('--min-evidence', type=int, default=3)
    a = ap.parse_args()
    with open(a.units, encoding='utf-8') as f:
        units = json.load(f)
    counts = {}
    for u in units:
        db, java, ev = score(u)
        ratio = db / max(java, 0.5)
        reasons = [f'db_score={db:.1f}', f'java_score={java:.1f}',
                   f'ratio={ratio:.2f}', f'sql={u["metrics"]["sql_literals"]}',
                   f'procs={u["metrics"]["proc_calls"]}',
                   f'db_objects={len(u["db_objects"])}',
                   f'loc={u["metrics"]["loc"]}']
        if u['linkage'] == 'missing' or ev < a.min_evidence:
            label = 'NEEDS_REVIEW'
            reasons.append('thin evidence or unlinked job command')
        elif ratio >= a.db_heavy_ratio:
            label = 'DB_HEAVY'
        elif ratio <= a.java_heavy_ratio:
            label = 'JAVA_HEAVY'
        else:
            label = 'BALANCED'
        u['preclassification'] = {'label': label, 'db_score': round(db, 1),
                                  'java_score': round(java, 1),
                                  'ratio': round(ratio, 2), 'reasons': reasons}
        counts[label] = counts.get(label, 0) + 1
    with open(a.units, 'w', encoding='utf-8') as f:
        json.dump(units, f, indent=1)
    print('pre-classification:', counts)


if __name__ == '__main__':
    main()

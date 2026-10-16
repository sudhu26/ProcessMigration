#!/usr/bin/env python3
"""Build review batches from units.json, skipping units that already have a
checkpoint file. Stdlib only.

Usage: make_batches.py --units state/graph/units.json --checkpoints state/units
                       --batch-size 12 [--only-labels NEEDS_REVIEW,UNKNOWN]
                       [--limit 50] --out /tmp/batches.json

Writes a JSON array of arrays (batches of unit_ids) to --out, and prints a
summary. Pass the file's content as the `batches` arg when launching the
oracle-migration-review workflow.
"""
import argparse, glob, json, os, re


def sanitize(n):
    return re.sub(r'[^A-Za-z0-9_.\-]', '_', n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--units', required=True)
    ap.add_argument('--checkpoints', required=True)
    ap.add_argument('--batch-size', type=int, default=12)
    ap.add_argument('--only-labels', default='',
                    help='comma-separated preclassification labels to include')
    ap.add_argument('--limit', type=int, default=0,
                    help='max units total (0 = no limit)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    units = json.load(open(a.units, encoding='utf-8'))
    done = {os.path.basename(f)[:-5]
            for f in glob.glob(os.path.join(a.checkpoints, '*.json'))}
    only = {l.strip() for l in a.only_labels.split(',') if l.strip()}

    pending = []
    for u in units:
        if sanitize(u['unit_id']) in done:
            continue
        if only and u.get('preclassification', {}).get('label') not in only:
            continue
        pending.append(u['unit_id'])
    if a.limit:
        pending = pending[:a.limit]

    batches = [pending[i:i + a.batch_size]
               for i in range(0, len(pending), a.batch_size)]
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(batches, f)
    print(f'{len(units)} units total, {len(done)} checkpointed, '
          f'{len(pending)} pending -> {len(batches)} batches -> {a.out}')


if __name__ == '__main__':
    main()

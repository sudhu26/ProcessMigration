#!/usr/bin/env python3
"""Merge scanner outputs into dependency graphs and per-process units.
Stdlib only.

Usage: build_graph.py --java java.json --plsql plsql.json --jobs jobs.json
                      --outdir <dir>
Writes: dependency-graph.json, units.json, db-object-catalog.json
"""
import argparse, json, os
from collections import defaultdict


def load(p, default):
    if p and os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--java', default='')
    ap.add_argument('--plsql', default='')
    ap.add_argument('--jobs', default='')
    ap.add_argument('--outdir', required=True)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    java_files = load(a.java, [])
    plsql_files = load(a.plsql, [])
    jobs = load(a.jobs, [])

    nodes, edges = {}, []
    def node(nid, kind, name, repo=''):
        if nid not in nodes:
            nodes[nid] = {'id': nid, 'kind': kind, 'name': name, 'repo': repo}

    # --- class index: FQN -> record, simple name -> fqns ---
    fqn_index, simple_index = {}, defaultdict(list)
    for rec in java_files:
        for cls in rec['classes']:
            fqn = f"{rec['package']}.{cls}" if rec['package'] else cls
            fqn_index[fqn] = rec
            simple_index[cls].append(fqn)
            node(f'java:{fqn}', 'java_class', fqn, rec['repo'])

    # --- db object index ---
    db_defs = {}
    for rec in plsql_files:
        for d in rec['definitions']:
            db_defs.setdefault(d['name'], d)
            node(f"db:{d['name']}", 'db_object', d['name'], rec['repo'])
    # also index objects only ever referenced (not defined in scan)
    def db_node(name, repo=''):
        node(f'db:{name}', 'db_object', name, repo)

    # --- per-DB-object read/write sets, for transitive data-flow inference ---
    db_access = {}
    for rec in plsql_files:
        for d in rec['definitions']:
            acc = db_access.setdefault(d['name'], {'reads': set(), 'writes': set()})
            acc['reads'].update(rec.get('reads', []))
            acc['writes'].update(rec.get('writes', []))

    # --- java -> java (imports) and java -> db edges ---
    file_edges = defaultdict(list)
    for rec in java_files:
        src = None
        fqns = [f"{rec['package']}.{c}" if rec['package'] else c
                for c in rec['classes']]
        for imp in rec['imports']:
            if imp in fqn_index:
                for fqn in fqns:
                    edges.append({'src': f'java:{fqn}', 'dst': f'java:{imp}',
                                  'kind': 'imports'})
                    file_edges[rec['path']].append(fqn_index[imp]['path'])
        for fqn in fqns:
            for t in rec['tables_read']:
                db_node(t); edges.append({'src': f'java:{fqn}', 'dst': f'db:{t}',
                                          'kind': 'reads'})
            for t in rec['tables_written']:
                db_node(t); edges.append({'src': f'java:{fqn}', 'dst': f'db:{t}',
                                          'kind': 'writes'})
            for p in rec['procs_called']:
                db_node(p); edges.append({'src': f'java:{fqn}', 'dst': f'db:{p}',
                                          'kind': 'calls'})

    # --- db -> db (plsql references / calls) ---
    for rec in plsql_files:
        owners = [d['name'] for d in rec['definitions']] or [rec['path']]
        for owner in owners:
            db_node(owner, rec['repo'])
            for ref in rec['references']:
                if ref == owner:
                    continue
                db_node(ref)
                edges.append({'src': f'db:{owner}', 'dst': f'db:{ref}',
                              'kind': 'references'})
            for call in rec['calls']:
                if call == owner:
                    continue
                db_node(call)
                edges.append({'src': f'db:{owner}', 'dst': f'db:{call}',
                              'kind': 'calls'})

    # --- jobs -> code (fallback: main() classes become units when no jobs) ---
    if not jobs:
        seen_fqn = set()
        for rec in java_files:
            if rec['metrics'].get('has_main'):
                for cls in rec['classes'][:1]:
                    fqn = f"{rec['package']}.{cls}" if rec['package'] else cls
                    if fqn not in seen_fqn:
                        seen_fqn.add(fqn)
                        jobs.append({'job_name': f'main:{fqn}', 'command': '',
                                     'host': '', 'schedule': '', 'depends_on': [],
                                     'main_class': fqn, 'scripts': [], 'jars': []})
    for job in jobs:
        jid = f"job:{job['job_name']}"
        node(jid, 'job', job['job_name'])
        mc = job.get('main_class')
        if mc and mc in fqn_index:
            edges.append({'src': jid, 'dst': f'java:{mc}', 'kind': 'invokes'})
        elif mc:
            cands = simple_index.get(mc.split('.')[-1], [])
            for c in cands[:3]:
                edges.append({'src': jid, 'dst': f'java:{c}',
                              'kind': 'invokes_maybe'})
        for s in job.get('scripts', []):
            sid = f'script:{s}'
            node(sid, 'script', s)
            edges.append({'src': jid, 'dst': sid, 'kind': 'invokes'})
        for dep in job.get('depends_on', []):
            node(f'job:{dep}', 'job', dep)
            edges.append({'src': jid, 'dst': f'job:{dep}', 'kind': 'depends_on'})
        for p in job.get('parent_jobs', []):
            node(f'job:{p}', 'job', p)
            edges.append({'src': jid, 'dst': f'job:{p}', 'kind': 'child_of'})

    # --- per-process units: entry files + import closure ---
    path_index = {r['path']: r for r in java_files}
    invokes = defaultdict(list)
    for e in edges:
        if e['kind'] in ('invokes', 'invokes_maybe') and e['src'].startswith('job:'):
            invokes[e['src']].append(e['dst'])

    units = []
    for job in jobs:
        jid = f"job:{job['job_name']}"
        entry_paths = []
        for dst in invokes.get(jid, []):
            if dst.startswith('java:'):
                rec = fqn_index.get(dst[5:])
                if rec:
                    entry_paths.append(rec['path'])
        # import closure, depth-limited
        seen, frontier = set(entry_paths), list(entry_paths)
        for _ in range(3):
            nxt = []
            for p in frontier:
                for q in file_edges.get(p, []):
                    if q not in seen:
                        seen.add(q); nxt.append(q)
            frontier = nxt
        files = [path_index[p] for p in seen if p in path_index]

        agg = {'loc': 0, 'branches': 0, 'methods': 0, 'sql_literals': 0,
               'jdbc_sites': 0, 'proc_calls': 0, 'oracle_ism_total': 0,
               'writes': 0}
        tables, procs, isms, repos = {}, set(), defaultdict(int), set()
        for r in files:
            m = r['metrics']
            agg['loc'] += m['loc']; agg['branches'] += m['branches']
            agg['methods'] += m['methods_rough']
            agg['sql_literals'] += r['sql_literal_count']
            agg['jdbc_sites'] += sum(r['jdbc'].values())
            agg['proc_calls'] += len(r['procs_called'])
            agg['oracle_ism_total'] += sum(r['oracle_isms'].values())
            agg['writes'] += sum(r['jdbc'].get(k, 0)
                                 for k in ('executeUpdate', 'executeBatch', 'addBatch'))
            for t in r['tables_read']:
                tables.setdefault(t, 'read')
            for t in r['tables_written']:
                tables[t] = 'write'
            procs.update(r['procs_called'])
            for k, v in r['oracle_isms'].items():
                isms[k] += v
            repos.add(r['repo'])
        # transitive DB access through called PL/SQL objects (one hop):
        # a unit that calls a proc effectively reads/writes what the proc touches
        for p in sorted(procs):
            acc = db_access.get(p) or db_access.get(p.split('.')[0])
            if not acc:
                continue
            for t in acc['reads']:
                tables.setdefault(t, 'read_via_proc')
            for t in acc['writes']:
                if tables.get(t) in (None, 'read', 'read_via_proc'):
                    tables[t] = 'write_via_proc'
        db_objects = [{'name': t, 'kind': k} for t, k in sorted(tables.items())]
        db_objects += [{'name': p, 'kind': 'call'} for p in sorted(procs)]

        units.append({
            'unit_id': job['job_name'], 'job_name': job['job_name'],
            'command': job.get('command', ''), 'schedule': job.get('schedule', ''),
            'depends_on': job.get('depends_on', []),
            'parent_jobs': job.get('parent_jobs', []),
            'entry_points': sorted(set(entry_paths)),
            'java_file_count': len(files),
            'java_files': [{'path': r['path'], 'repo': r['repo'],
                            'classes': r['classes']} for r in files],
            'metrics': agg,
            'db_objects': db_objects,
            'oracle_isms': dict(isms),
            'repos': sorted(repos),
            'linkage': 'ok' if entry_paths else 'missing',
        })

    # --- data-flow inference: if unit A writes a table that unit B reads,
    #     B likely depends on A. Heuristic ordering for wave planning when no
    #     scheduler data exists; agents/humans validate during review. ---
    READ_KINDS = ('read', 'read_via_proc')
    WRITE_KINDS = ('write', 'write_via_proc')
    unit_reads, unit_writes = {}, {}
    for u in units:
        unit_reads[u['unit_id']] = {d['name'] for d in u['db_objects']
                                    if d['kind'] in READ_KINDS}
        unit_writes[u['unit_id']] = {d['name'] for d in u['db_objects']
                                     if d['kind'] in WRITE_KINDS}
    writers_of = defaultdict(set)
    for uid, ts in unit_writes.items():
        for t in ts:
            writers_of[t].add(uid)
    required_by = defaultdict(set)
    for u in units:
        rid = u['unit_id']
        upstream = defaultdict(set)
        for t in unit_reads[rid]:
            for wid in writers_of.get(t, ()):
                if wid != rid:
                    upstream[wid].add(t)
        u['likely_depends_on'] = sorted(upstream)
        for wid in sorted(upstream):
            required_by[wid].add(rid)
            edges.append({'src': f'job:{rid}', 'dst': f'job:{wid}',
                          'kind': 'likely_depends_on',
                          'via': sorted(upstream[wid])})
    for u in units:
        u['likely_required_by'] = sorted(required_by.get(u['unit_id'], []))
    n_inferred = sum(len(u['likely_depends_on']) for u in units)

    with open(os.path.join(a.outdir, 'dependency-graph.json'), 'w') as f:
        json.dump({'nodes': list(nodes.values()), 'edges': edges}, f, indent=1)
    with open(os.path.join(a.outdir, 'units.json'), 'w') as f:
        json.dump(units, f, indent=1)
    catalog = [{'object': name, 'kind': d['kind'],
                'defined_in': [r['path'] for r in plsql_files
                               if any(x['name'] == name for x in r['definitions'])]}
               for name, d in sorted(db_defs.items())]
    with open(os.path.join(a.outdir, 'db-object-catalog.json'), 'w') as f:
        json.dump(catalog, f, indent=1)
    print(f'{len(nodes)} nodes, {len(edges)} edges, {len(units)} units, '
          f'{n_inferred} inferred data-flow dependencies -> {a.outdir}')


if __name__ == '__main__':
    main()

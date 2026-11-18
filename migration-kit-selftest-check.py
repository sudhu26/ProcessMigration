#!/usr/bin/env python3
"""Portable self-test for the final migration kit (from the independent
verifier's checker; only the path resolution and M11 changed).
Independent final verification of the migration kit (NOT the fixer's checker).
Runs the full pipeline from scratch in /tmp/verify-final and checks all 46
section-6 assertions against real output, plus a mechanism-diff checklist
derived from section 4. Read-only with respect to the kit.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

# --- Portable path resolution (no editing needed on your Mac) -----------------
# KIT: the folder holding scripts/, fixtures/, README.md. Resolved in order:
#   1. $MIGRATION_KIT_DIR   2. the folder this checker sits in (if it has scripts/)
#   3. a sibling folder named migration-kit   4. ~/workspace/migration-kit
# PROMPT / ZIP: the shipped build prompt and kit zip, searched for next to this
# checker, next to the kit, in ~/workspace and in ~/Downloads; override with
# $MIGRATION_KIT_PROMPT / $MIGRATION_KIT_ZIP. M11 verifies whichever of the
# shipped artifacts it finds and says plainly which it could not find.
_HERE = os.path.dirname(os.path.abspath(__file__))
def _first_existing(cands):
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None
def _resolve_kit():
    env = os.environ.get('MIGRATION_KIT_DIR')
    if env and os.path.isdir(os.path.join(env, 'scripts')):
        return os.path.abspath(env)
    if os.path.isdir(os.path.join(_HERE, 'scripts')):
        return _HERE
    sib = os.path.join(_HERE, 'migration-kit')
    if os.path.isdir(os.path.join(sib, 'scripts')):
        return sib
    return os.path.expanduser('~/workspace/migration-kit')
KIT = _resolve_kit()
# The original verifier checker silently depends on the caller's working
# directory: load_ignore('fixtures/ignore.json') and several fixture paths
# are relative, so running it from anywhere but the kit folder fails A12
# and M10c even though the kit is fine (verified 2026-11-18: 47/47 from
# inside the kit, 45/47 + 15/16 from /tmp). Pin the cwd to the kit so the
# self-test passes from any directory, as the handover instructions imply.
os.chdir(KIT)
V = '/tmp/verify-final'
PROMPT = _first_existing([
    os.environ.get('MIGRATION_KIT_PROMPT'),
    os.path.join(_HERE, 'migration-kit-build-prompt.md'),
    os.path.join(os.path.dirname(KIT), 'migration-kit-build-prompt.md'),
    os.path.expanduser('~/workspace/migration-kit-build-prompt.md'),
    os.path.expanduser('~/Downloads/migration-kit-build-prompt.md'),
])
ZIP_CANDIDATES = [
    os.environ.get('MIGRATION_KIT_ZIP'),
    os.path.join(_HERE, 'migration-kit.zip'),
    os.path.join(os.path.dirname(KIT), 'migration-kit.zip'),
    os.path.expanduser('~/workspace/migration-kit.zip'),
    os.path.expanduser('~/Downloads/migration-kit.zip'),
]
# Files that belong to this self-test, not to the shipped kit: excluded from
# the M11 disk walk when the checker lives inside the kit folder.
SELFTEST_FILES = {'migration-kit-selftest-check.py', 'run_selftest.sh'}
sys.path.insert(0, os.path.join(KIT, 'scripts'))

RESULTS = []  # (kind, id, name, ok, evidence)

def check(kind, cid, name, ok, evidence=''):
    RESULTS.append((kind, cid, name, bool(ok), str(evidence)[:600]))
    print(f"[{'PASS' if ok else 'FAIL'}] {kind} {cid}: {name} :: {str(evidence)[:220]}")

def run(cmd, env_extra=None, cwd=KIT, expect=0):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if expect is not None and p.returncode != expect:
        print(f"  !! cmd failed rc={p.returncode}: {' '.join(cmd)}\n{p.stdout[-800:]}\n{p.stderr[-800:]}")
    return p

def load(path):
    with open(path) as f:
        return json.load(f)

def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()

def rec_by_path(records, suffix):
    for r in records:
        if r['path'].endswith(suffix):
            return r
    return None

def unit_by_id(units, uid):
    for u in units:
        if u['unit_id'] == uid:
            return u
    return None

os.makedirs(V, exist_ok=True)
for d in ['raw', 'raw-ig', 'graph', 'graph-ig', 'graph-empty', 'docs', 'tree', 'tree3',
          'tree-ig', 'tree-pat', 'tree-nopat', 'tree-nosemi', 'tree-commented',
          'tree-semi', 'tree-args', 'tree-only-empty', 'output', 'units', 'ckpt',
          'single', 'det']:
    os.makedirs(os.path.join(V, d), exist_ok=True)

PY = sys.executable
S = os.path.join(KIT, 'scripts')

# ---------------- pipeline: scan ----------------
for repo, root in [('Risk_DB', 'fixtures/Risk_DB'), ('Risk_DL', 'fixtures/Risk_DL'),
                   ('CoreWM', 'fixtures/CoreWM'), ('scripts', 'fixtures/scripts')]:
    run([PY, f'{S}/scan_java.py', '--root', root, '--repo', repo,
         '--out', f'{V}/raw/java_{repo}.json'])
    run([PY, f'{S}/scan_plsql.py', '--root', root, '--repo', repo,
         '--exts', '.sql,.pls,.pks,.pkb,.fnc,.prc,.trg,.vw,.tab,.seq',
         '--out', f'{V}/raw/plsql_{repo}.json'])
    run([PY, f'{S}/scan_scripts.py', '--root', root, '--repo', repo,
         '--out', f'{V}/raw/scripts_{repo}.json'])
run([PY, f'{S}/scan_jobs.py', '--export', 'fixtures/autosys/jobs.csv',
     '--out', f'{V}/raw/jobs.json'])
run([PY, f'{S}/scan_jobs.py', '--export', 'fixtures/controlm/jobs.xml',
     '--out', f'{V}/raw/jobs_cm.json'])
run([PY, f'{S}/scan_jobs.py', '--export', 'fixtures/autosys/jobs.csv',
     '--format', 'autosys', '--out', f'{V}/raw/jobs_auto.json'])

# merge (sorted glob, as the README specifies)
merge_code = (
    "import json,glob; "
    "j=[]; [j.extend(json.load(open(f))) for f in sorted(glob.glob('%s/raw/java_*.json')) if not f.endswith('_all.json')]; "
    "json.dump(j,open('%s/raw/java_all.json','w')); "
    "p=[]; [p.extend(json.load(open(f))) for f in sorted(glob.glob('%s/raw/plsql_*.json')) if not f.endswith('_all.json')]; "
    "json.dump(p,open('%s/raw/plsql_all.json','w')); "
    "s=[]; [s.extend(json.load(open(f))) for f in sorted(glob.glob('%s/raw/scripts_*.json')) if not f.endswith('_all.json')]; "
    "json.dump(s,open('%s/raw/scripts_all.json','w'))" % ((V,) * 6)
)
run([PY, '-c', merge_code])

# ---------------- pipeline: graph / classify / docs / catalog / tree / reports ----------------
run([PY, f'{S}/build_graph.py', '--java', f'{V}/raw/java_all.json',
     '--plsql', f'{V}/raw/plsql_all.json', '--jobs', f'{V}/raw/jobs.json',
     '--scripts', f'{V}/raw/scripts_all.json', '--outdir', f'{V}/graph'])
run([PY, f'{S}/classify.py', '--units', f'{V}/graph/units.json',
     '--db-heavy-ratio', '1.6', '--java-heavy-ratio', '0.6', '--min-evidence', '3'])
run([PY, f'{S}/build_process_docs.py', '--units', f'{V}/graph/units.json',
     '--graph', f'{V}/graph/dependency-graph.json', '--java', f'{V}/raw/java_all.json',
     '--outdir', f'{V}/docs'])
run([PY, f'{S}/build_table_catalog.py', '--units', f'{V}/graph/units.json',
     '--plsql', f'{V}/raw/plsql_all.json', '--out', f'{V}/tables.md'])
run([PY, f'{S}/prepare_migration_tree.py',
     '--repos', 'Risk_DB=fixtures/Risk_DB,Risk_DL=fixtures/Risk_DL,CoreWM=fixtures/CoreWM,scripts=fixtures/scripts',
     '--outdir', f'{V}/tree'])
run([PY, f'{S}/prepare_migration_tree.py',
     '--repos', 'Risk_DB=fixtures/Risk_DB,Risk_DL=fixtures/Risk_DL,CoreWM=fixtures/CoreWM',
     '--outdir', f'{V}/tree3'])
run([PY, f'{S}/compile_reports.py', '--units', f'{V}/graph/units.json',
     '--graph', f'{V}/graph/dependency-graph.json',
     '--catalog', f'{V}/graph/db-object-catalog.json',
     '--checkpoints', f'{V}/units', '--java', f'{V}/raw/java_all.json',
     '--plsql', f'{V}/raw/plsql_all.json', '--outdir', f'{V}/output'])

# jobs: [] run
with open(f'{V}/raw/jobs_empty.json', 'w') as f:
    json.dump([], f)
run([PY, f'{S}/build_graph.py', '--java', f'{V}/raw/java_all.json',
     '--plsql', f'{V}/raw/plsql_all.json', '--jobs', f'{V}/raw/jobs_empty.json',
     '--scripts', f'{V}/raw/scripts_all.json', '--outdir', f'{V}/graph-empty'])

# single-process run (assertion 14)
run([PY, f'{S}/run_process.py', '--process', 'NIGHTLY_RISK_JOB',
     '--repos', 'Risk_DB=fixtures/Risk_DB,Risk_DL=fixtures/Risk_DL,CoreWM=fixtures/CoreWM,scripts=fixtures/scripts',
     '--jobs', 'fixtures/autosys/jobs.csv', '--ignore', 'fixtures/ignore.json',
     '--outdir', f'{V}/single'])

# --ignore scans + graph + tree
for repo, root in [('Risk_DB', 'fixtures/Risk_DB'), ('Risk_DL', 'fixtures/Risk_DL'),
                   ('CoreWM', 'fixtures/CoreWM'), ('scripts', 'fixtures/scripts')]:
    run([PY, f'{S}/scan_java.py', '--root', root, '--repo', repo,
         '--ignore', 'fixtures/ignore.json', '--out', f'{V}/raw-ig/java_{repo}.json'])
    run([PY, f'{S}/scan_plsql.py', '--root', root, '--repo', repo,
         '--exts', '.sql,.pls,.pks,.pkb,.fnc,.prc,.trg,.vw,.tab,.seq',
         '--ignore', 'fixtures/ignore.json', '--out', f'{V}/raw-ig/plsql_{repo}.json'])
    run([PY, f'{S}/scan_scripts.py', '--root', root, '--repo', repo,
         '--out', f'{V}/raw-ig/scripts_{repo}.json'])
merge_ig = (
    "import json,glob; "
    "j=[]; [j.extend(json.load(open(f))) for f in sorted(glob.glob('%s/raw-ig/java_*.json')) if not f.endswith('_all.json')]; "
    "json.dump(j,open('%s/raw-ig/java_all.json','w')); "
    "p=[]; [p.extend(json.load(open(f))) for f in sorted(glob.glob('%s/raw-ig/plsql_*.json')) if not f.endswith('_all.json')]; "
    "json.dump(p,open('%s/raw-ig/plsql_all.json','w')); "
    "s=[]; [s.extend(json.load(open(f))) for f in sorted(glob.glob('%s/raw-ig/scripts_*.json')) if not f.endswith('_all.json')]; "
    "json.dump(s,open('%s/raw-ig/scripts_all.json','w'))" % ((V,) * 6)
)
run([PY, '-c', merge_ig])
run([PY, f'{S}/build_graph.py', '--java', f'{V}/raw-ig/java_all.json',
     '--plsql', f'{V}/raw-ig/plsql_all.json', '--jobs', f'{V}/raw/jobs.json',
     '--scripts', f'{V}/raw-ig/scripts_all.json', '--ignore', 'fixtures/ignore.json',
     '--outdir', f'{V}/graph-ig'])
run([PY, f'{S}/prepare_migration_tree.py',
     '--repos', 'Risk_DB=fixtures/Risk_DB,Risk_DL=fixtures/Risk_DL,CoreWM=fixtures/CoreWM,scripts=fixtures/scripts',
     '--ignore', 'fixtures/ignore.json', '--outdir', f'{V}/tree-ig'])

# edge-case scans
run([PY, f'{S}/scan_java.py', '--root', 'fixtures/edge-cases', '--repo', 'EdgeCases',
     '--out', f'{V}/edge_java.json'])
run([PY, f'{S}/scan_plsql.py', '--root', 'fixtures/edge-cases', '--repo', 'EdgeCases',
     '--exts', '.sql', '--out', f'{V}/edge_plsql.json'])
run([PY, f'{S}/scan_java.py', '--root', 'fixtures/edge-cases', '--repo', 'EdgeCases',
     '--ignore', 'fixtures/ignore.json', '--out', f'{V}/edge_java_ig.json'])
run([PY, f'{S}/scan_scripts.py', '--root', 'fixtures/scripts', '--repo', 'Scripts',
     '--out', f'{V}/scripts_fixtures.json'])
run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'edge=fixtures/edge-cases',
     '--outdir', f'{V}/tree-args'])
# --only without --units/--graph/--catalog (assertion 26)
p26 = run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'Risk_DB=fixtures/Risk_DB',
           '--only', 'NIGHTLY_RISK_JOB', '--outdir', f'{V}/tree-only-empty'])

# patterns
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs.json',
     '--migrated-dir', 'fixtures/exemplars/migrated', '--approve', 'auto',
     '--out', f'{V}/patterns.json'])
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs.json',
     '--migrated-dir', 'fixtures/exemplars/migrated',
     '--out', f'{V}/patterns_prop.json'])
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs.json',
     '--migrated-dir', 'fixtures/exemplars/migrated', '--base', f'{V}/patterns.json',
     '--out', f'{V}/patterns_base.json'])
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs.json',
     '--migrated-dir', 'fixtures/exemplars/migrated', '--out', f'{V}/p1.json'])
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs.json',
     '--migrated-dir', 'fixtures/exemplars/migrated', '--base', f'{V}/p1.json',
     '--approve', 'auto', '--out', f'{V}/p2.json'])
run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'ex=fixtures/exemplars/onprem',
     '--patterns', f'{V}/patterns.json', '--outdir', f'{V}/tree-pat'])
run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'ex=fixtures/exemplars/onprem',
     '--outdir', f'{V}/tree-nopat'])
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs-nosemi.json',
     '--approve', 'auto', '--out', f'{V}/patterns_nosemi.json'])
run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'ex=fixtures/exemplars/onprem',
     '--patterns', f'{V}/patterns_nosemi.json', '--outdir', f'{V}/tree-nosemi'])
# commented pair (assertion 40)
with open(f'{V}/pairs_commented.json', 'w') as f:
    json.dump([{'approved': True,
                'onprem': 'fixtures/exemplars/onprem/commented_merge.sql',
                'migrated': 'fixtures/exemplars/migrated/merge_load.sql'}], f)
run([PY, f'{S}/learn_patterns.py', '--pairs', f'{V}/pairs_commented.json',
     '--approve', 'auto', '--out', f'{V}/patterns_commented.json'])
run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'ex=fixtures/exemplars/onprem',
     '--patterns', f'{V}/patterns_commented.json', '--outdir', f'{V}/tree-commented'])
# reverse-terminator pair (assertion 45)
run([PY, f'{S}/learn_patterns.py', '--pairs', 'fixtures/exemplars/pairs-semi.json',
     '--approve', 'auto', '--out', f'{V}/patterns_semi.json'])
run([PY, f'{S}/prepare_migration_tree.py', '--repos', 'ex=fixtures/exemplars/onprem',
     '--patterns', f'{V}/patterns_semi.json', '--outdir', f'{V}/tree-semi'])
# wrapped-only jobs (assertion 42)
run([PY, f'{S}/scan_jobs.py', '--export', 'fixtures/autosys/jobs-wrapped-only.csv',
     '--out', f'{V}/raw/jobs_wrapped_only.json'])
run([PY, f'{S}/build_graph.py', '--java', f'{V}/raw/java_all.json',
     '--plsql', f'{V}/raw/plsql_all.json', '--jobs', f'{V}/raw/jobs_wrapped_only.json',
     '--scripts', f'{V}/raw/scripts_all.json', '--outdir', f'{V}/graph-wrapped-only'])

# determinism: PYTHONHASHSEED 0..7
det_md5 = {}
for seed in range(8):
    outd = f'{V}/det/seed{seed}'
    os.makedirs(outd, exist_ok=True)
    run([PY, f'{S}/build_graph.py', '--java', f'{V}/raw/java_all.json',
         '--plsql', f'{V}/raw/plsql_all.json', '--jobs', f'{V}/raw/jobs.json',
         '--scripts', f'{V}/raw/scripts_all.json', '--outdir', outd],
        env_extra={'PYTHONHASHSEED': str(seed)})
    det_md5[seed] = md5(f'{outd}/units.json')

# make_batches (assertion 6): one checkpoint present
with open(f'{V}/ckpt/NIGHTLY_RISK_JOB.json', 'w') as f:
    json.dump({'unit_id': 'NIGHTLY_RISK_JOB', 'verdict': 'OK'}, f)
b6 = run([PY, f'{S}/make_batches.py', '--units', f'{V}/graph/units.json',
          '--checkpoints', f'{V}/ckpt', '--out', f'{V}/batches.json'])
b6nr = run([PY, f'{S}/make_batches.py', '--units', f'{V}/graph/units.json',
            '--checkpoints', f'{V}/ckpt', '--only-labels', 'NEEDS_REVIEW',
            '--out', f'{V}/batches_nr.json'])

print('--- pipeline done ---')

# ================= assertion checks =================
java_all = load(f'{V}/raw/java_all.json')
plsql_all = load(f'{V}/raw/plsql_all.json')
units = load(f'{V}/graph/units.json')
graph = load(f'{V}/graph/dependency-graph.json')
catalog = load(f'{V}/graph/db-object-catalog.json')
units_empty = load(f'{V}/graph-empty/units.json')
graph_empty = load(f'{V}/graph-empty/dependency-graph.json')
units_wrapped = load(f'{V}/graph-wrapped-only/units.json')
edge_java = load(f'{V}/edge_java.json')
edge_plsql = load(f'{V}/edge_plsql.json')
edge_java_ig = load(f'{V}/edge_java_ig.json')
jobs = load(f'{V}/raw/jobs.json')
jobs_cm = load(f'{V}/raw/jobs_cm.json')
nightly = rec_by_path(java_all, 'NightlyRiskJob.java')
dbutil = rec_by_path(java_all, 'DbUtil.java')
nightly_unit = unit_by_id(units, 'NIGHTLY_RISK_JOB')

# 1
ism = nightly.get('oracle_isms', {})
check('A', 1, 'scan_java fixtures',
      ism.get('ROWNUM') == 1 and ism.get('SYSDATE_SYSTIMESTAMP') == 1 and ism.get('NVL') == 1
      and nightly.get('tables_read') == ['RISK_POSITIONS', 'RISK_RESULTS']
      and 'RISK_CALC_PKG.CALC_EXPOSURE' in nightly.get('procs_called', [])
      and any('DbUtil' in i for i in nightly.get('imports', []))
      and nightly.get('metrics', {}).get('has_main') is True,
      f"isms={ism} tables_read={nightly.get('tables_read')} procs={nightly.get('procs_called')} has_main={nightly.get('metrics', {}).get('has_main')}")

# 2
defs = set()
writes = set()
risky = {}
calls = set()
for r in plsql_all:
    for d in r.get('definitions', []):
        defs.add(d.get('kind'))
    writes.update(r.get('writes', []))
    for k, v in r.get('risky_constructs', {}).items():
        risky[k] = risky.get(k, 0) + v
    calls.update(r.get('calls', []))
check('A', 2, 'scan_plsql definitions/writes/risky',
      {'TABLE', 'VIEW', 'PACKAGE BODY'} <= defs and 'RISK_RESULTS' in writes
      and 'execute_immediate' in risky and 'EXECUTE IMMEDIATE' not in str(calls),
      f"defs={sorted(defs)} writes has RISK_RESULTS={('RISK_RESULTS' in writes)} risky={risky} calls={sorted(calls)}")

# 3
nightly_job = next((j for j in jobs if j.get('job_name') == 'NIGHTLY_RISK_JOB' or j.get('JOB_NAME') == 'NIGHTLY_RISK_JOB'), None)
dep = None
if nightly_job:
    dep = nightly_job.get('depends_on') or nightly_job.get('CONDITION')
check('A', 3, 'scan_jobs autosys + controlm',
      len(jobs) == 5 and dep == ['FEED_LANDED', 'MARKET_DATA_READY'] and len(jobs_cm) == 2,
      f"autosys jobs={len(jobs)} nightly depends_on={dep} controlm jobs={len(jobs_cm)}")

# 4
synth_empty = sorted([u['unit_id'] for u in units_empty if u.get('synthetic')])
rr = unit_by_id(units_empty, 'main:com.risk.wm.jobs.RiskReportJob')
nn = unit_by_id(units_empty, 'main:com.risk.wm.jobs.NightlyRiskJob')
edge_via = [e for e in graph_empty.get('edges', [])
            if e.get('kind') == 'likely_depends_on' and 'RiskReportJob' in e.get('src', '')
            and 'NightlyRiskJob' in e.get('dst', '')]
rr_obj = None
if nn:
    rr_obj = next((o for o in nn.get('db_objects', []) if o.get('name') == 'RISK_RESULTS'), None)
check('A', 4, 'jobs [] synthetic units + likely edge',
      synth_empty == ['main:LegacyJob', 'main:com.risk.wm.jobs.NightlyRiskJob', 'main:com.risk.wm.jobs.RiskReportJob']
      and rr is not None and 'main:com.risk.wm.jobs.NightlyRiskJob' in rr.get('likely_depends_on', [])
      and edge_via and edge_via[0].get('via') == ['RISK_RESULTS']
      and rr_obj is not None and rr_obj.get('kind') == 'read'
      and rr_obj.get('transitive_write') == 'write_via_proc',
      f"synthetic={synth_empty} rr.likely={rr.get('likely_depends_on') if rr else None} via={edge_via[0].get('via') if edge_via else None} RISK_RESULTS={rr_obj}")

# 5
pre = {u['unit_id']: (u.get('preclassification') or {}).get('label') for u in units}
check('A', 5, 'classify labels',
      pre.get('NIGHTLY_RISK_JOB') in ('BALANCED', 'DB_HEAVY')
      and pre.get('RISK_REPORT_JOB') == 'NEEDS_REVIEW'
      and pre.get('STALE_JOB') == 'NEEDS_REVIEW',
      f"labels={pre}")

# 6
batches = load(f'{V}/batches.json')
batches_nr = load(f'{V}/batches_nr.json')
flat = [u for b in batches for u in (b if isinstance(b, list) else b.get('units', []))]
flat_nr = [u for b in batches_nr for u in (b if isinstance(b, list) else b.get('units', []))]
check('A', 6, 'make_batches checkpoint exclusion + --only-labels',
      len(flat) == 5 and 'NIGHTLY_RISK_JOB' not in flat
      and sorted(flat_nr) == ['RISK_REPORT_JOB', 'STALE_JOB'],
      f"stdout={b6.stdout.strip()!r} pending={len(flat)} nr={sorted(flat_nr)}")

# 7
sh = rec_by_path(load(f'{V}/raw/scripts_all.json'), 'run_nightly.sh')
check('A', 7, 'scan_scripts wrapper',
      sh is not None and sh.get('java_mains') == ['com.risk.wm.jobs.NightlyRiskJob']
      and any('risk-dl.jar' in j for j in sh.get('jars', []))
      and any('env.sh' in s for s in sh.get('scripts_called', []))
      and any('post_load.sql' in s for s in sh.get('sql_files', [])),
      f"java_mains={sh.get('java_mains') if sh else None} jars={sh.get('jars') if sh else None} called={sh.get('scripts_called') if sh else None} sql={sh.get('sql_files') if sh else None}")

# 8
wrapped = unit_by_id(units, 'WRAPPED_JOB')
legacy = unit_by_id(units, 'LEGACY_JOB')
synth_rr = unit_by_id(units, 'main:com.risk.wm.jobs.RiskReportJob')
synth_nightly = unit_by_id(units, 'main:com.risk.wm.jobs.NightlyRiskJob')
wa_obj = next((o for o in (wrapped or {}).get('db_objects', []) if o.get('name') == 'RISK_AUDIT'), None)
check('A', 8, 'graph linkage + synthetic RiskReportJob',
      wrapped is not None and wrapped.get('linkage') == 'ok'
      and any('NightlyRiskJob.java' in f.get('path', '') for f in wrapped.get('java_files', []))
      and wa_obj is not None and wa_obj.get('kind') == 'write_via_script'
      and legacy is not None and legacy.get('linkage') == 'maybe'
      and legacy.get('linkage_candidates') == ['LegacyJob']
      and synth_rr is not None and synth_rr.get('synthetic') is True
      and synth_nightly is None,
      f"wrapped linkage={wrapped.get('linkage') if wrapped else None} RISK_AUDIT={wa_obj} legacy={legacy.get('linkage') if legacy else None}/{legacy.get('linkage_candidates') if legacy else None} synth_rr={synth_rr is not None} synth_nightly={synth_nightly is not None}")

# 9
ddl = rec_by_path(plsql_all, 'risk_tables.sql')
nu_pos = next((o for o in nightly_unit.get('db_objects', []) if o.get('name') == 'RISK_POSITIONS'), None)
nu_res = next((o for o in nightly_unit.get('db_objects', []) if o.get('name') == 'RISK_RESULTS'), None)
check('A', 9, 'column extraction',
      dbutil.get('table_columns', {}).get('RISK_POSITIONS', {}).get('write') == ['AS_OF_DATE', 'BOOK', 'EXPOSURE', 'POSITION_ID']
      and ddl.get('ddl_columns', {}).get('RISK_POSITIONS') == ['POSITION_ID', 'BOOK', 'EXPOSURE', 'AS_OF_DATE']
      and nightly.get('table_columns', {}).get('RISK_RESULTS', {}).get('read') == ['TOTAL_EXPOSURE']
      and set(['BOOK', 'EXPOSURE', '*']) <= set(nightly.get('table_columns', {}).get('RISK_POSITIONS', {}).get('read', []))
      and nu_pos is not None and 'P_ASOF' in nu_pos.get('columns_read', [])
      and nu_res is not None and nu_res.get('columns_written') == ['AS_OF_DATE', 'BOOK', 'TOTAL_EXPOSURE'],
      f"DbUtil write={dbutil.get('table_columns', {}).get('RISK_POSITIONS')} ddl={ddl.get('ddl_columns', {}).get('RISK_POSITIONS')} nightly RISK_RESULTS.read={nightly.get('table_columns', {}).get('RISK_RESULTS')} unit pos_read={nu_pos.get('columns_read') if nu_pos else None} res_written={nu_res.get('columns_written') if nu_res else None}")

# 10
docs = sorted(os.listdir(f'{V}/docs'))
nightly_html = open(f'{V}/docs/NIGHTLY_RISK_JOB.html').read() if os.path.exists(f'{V}/docs/NIGHTLY_RISK_JOB.html') else ''
tables_md = open(f'{V}/tables.md').read()
check('A', 10, 'process docs + table catalog',
      len([d for d in docs if d.endswith('.html')]) == 7 and 'index.html' in docs
      and 'DRAFT' in nightly_html and 'RISK_POSITIONS' in nightly_html and '<svg' in nightly_html
      and 'RISK_POSITIONS' in tables_md and 'POSITION_ID' in tables_md,
      f"docs={docs} nightly has DRAFT={'DRAFT' in nightly_html} svg={'<svg' in nightly_html}")

# 11 — translator probes
import translate_sql as ts
r_nvl = ts.translate("SELECT NVL(x, 0) FROM t")
r_sys = ts.translate("SELECT SYSDATE FROM DUAL")
r_td = ts.translate("SELECT TO_DATE('2026-01-01','YYYY-MM-DD') FROM DUAL")
r_dec = ts.translate("SELECT DECODE(a, 1, 'x', 'y') FROM t")
r_rn = ts.translate("SELECT * FROM t WHERE ROWNUM <= 5")
tree_nightly = open(f'{V}/tree/CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java').read()
check('A', 11, 'translate_sql + tree mirror',
      'COALESCE(x, 0)' in r_nvl.get('translated', '')
      and 'current_timestamp()' in r_sys.get('translated', '')
      and 'to_date' in r_td.get('translated', '') and 'yyyy-MM-dd' in r_td.get('translated', '')
      and r_td.get('confidence') == 'medium'
      and 'CASE' in r_dec.get('translated', '')
      and r_rn.get('confidence') == 'needs-review' and any('ROWNUM' in f for f in r_rn.get('flags', []))
      and 'COALESCE' in tree_nightly and 'current_timestamp()' in tree_nightly
      and 'TODO-SPARK: ROWNUM' in tree_nightly
      and os.path.exists(f'{V}/tree/translation-report.md'),
      f"nvl={r_nvl.get('translated')} sys={r_sys.get('translated')} td={r_td.get('translated')} conf={r_td.get('confidence')} dec={r_dec.get('translated')} rn conf={r_rn.get('confidence')} flags={r_rn.get('flags')}")

# 12 — ignore lists
java_ig = load(f'{V}/raw-ig/java_all.json')
plsql_ig = load(f'{V}/raw-ig/plsql_all.json')
graph_ig = load(f'{V}/graph-ig/dependency-graph.json')
nig = rec_by_path(java_ig, 'NightlyRiskJob.java')
pig = rec_by_path(plsql_ig, 'risk_calc_pkg.pkb')
from ignore_lists import load_ignore
ign = load_ignore('fixtures/ignore.json')
r_dbms = ts.translate("BEGIN DBMS_OUTPUT.PUT_LINE('x'); END;", ignored=ign)
r_rn2 = ts.translate("SELECT * FROM t WHERE ROWNUM <= 5", ignored=ign)
nodes_ig = {n.get('id', n.get('name', '')) for n in graph_ig.get('nodes', [])}
check('A', 12, 'ignore lists',
      nig is not None and 'oracle.jdbc.OracleDriver' in nig.get('ignored_imports', [])
      and 'oracle.jdbc.OracleDriver' not in nig.get('imports', [])
      and pig is not None and 'DBMS_OUTPUT.PUT_LINE' in pig.get('ignored_calls', [])
      and 'V$SESSION' in pig.get('ignored_tables', [])
      and 'dbms' not in {k.lower() for k in pig.get('risky_constructs', {})}
      and not any('V$SESSION' in n for n in nodes_ig)
      and not any('DBMS_OUTPUT' in n for n in nodes_ig)
      and r_dbms.get('flags') == [] and any('ROWNUM' in f for f in r_rn2.get('flags', [])),
      f"nig.ignored_imports={nig.get('ignored_imports') if nig else None} pig.ignored_calls={pig.get('ignored_calls') if pig else None} pig.ignored_tables={pig.get('ignored_tables') if pig else None} risky={pig.get('risky_constructs') if pig else None} dbms flags={r_dbms.get('flags')} rn flags={r_rn2.get('flags')}")

# 13
check('A', 13, 'no-ignore traces everything',
      'oracle.jdbc.OracleDriver' in nightly.get('imports', [])
      and any('V$SESSION' in t for t in sum([r.get('reads', []) for r in plsql_all], []))
      and any('DBMS_OUTPUT.PUT_LINE' in c for r in plsql_all for c in r.get('calls', [])),
      f"nightly imports has oracle.jdbc={('oracle.jdbc.OracleDriver' in nightly.get('imports', []))}")

# 14 — single-process run
s_units = load(f'{V}/single/state/graph/units.json')
s_docs = sorted(os.listdir(f'{V}/single/output/process-docs'))
s_tables = open(f'{V}/single/output/tables.md').read()
s_batches = load(f'{V}/single/batches.json')
s_tree_files = []
for root, _, files in os.walk(f'{V}/single/migration-tree'):
    for fn in files:
        if not fn.endswith('.translate.json') and fn != 'translation-report.md':
            s_tree_files.append(os.path.relpath(os.path.join(root, fn), f'{V}/single/migration-tree'))
s_unit = s_units[0] if len(s_units) == 1 else None
s_wa = next((o for o in (s_unit or {}).get('db_objects', []) if o.get('name') == 'RISK_AUDIT'), None)
s_out = sorted(os.listdir(f'{V}/single/output'))
s_tables_sections = [s for s in re.findall(r'^## (\S+)', s_tables, flags=re.M) if s != 'Summary']
check('A', 14, 'single-process run',
      len(s_units) == 1 and s_unit is not None and s_unit['unit_id'] == 'NIGHTLY_RISK_JOB'
      and len([d for d in s_docs if d.endswith('.html')]) == 2
      and sorted(s_tables_sections) == ['RISK_AUDIT', 'RISK_POSITIONS', 'RISK_RESULTS']
      and s_batches == [['NIGHTLY_RISK_JOB']]
      and sorted(s_tree_files) == sorted(['CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java',
          'Risk_DL/common/com/risk/dl/DbUtil.java', 'Risk_DB/ddl/risk_tables.sql',
          'Risk_DB/pkg/risk_calc_pkg.pkb', 'Risk_DB/sql/post_load.sql', 'scripts/run_nightly.sh'])
      and s_wa is not None and s_wa.get('kind') == 'write_via_script'
      and set(['process-catalog.csv', 'classification-report.md', 'oracle-dialect-census.csv', 'db-object-catalog.csv']) <= set(s_out),
      f"units={len(s_units)} docs={s_docs} tables={sorted(s_tables_sections)} batches={s_batches} tree={sorted(s_tree_files)} RISK_AUDIT={s_wa} output={s_out}")

# 15 — full run
tree_files = []
for root, _, files in os.walk(f'{V}/tree'):
    for fn in files:
        if not fn.endswith('.translate.json') and fn != 'translation-report.md':
            tree_files.append(os.path.relpath(os.path.join(root, fn), f'{V}/tree'))
tree3_files = []
for root, _, files in os.walk(f'{V}/tree3'):
    for fn in files:
        if not fn.endswith('.translate.json') and fn != 'translation-report.md':
            tree3_files.append(os.path.relpath(os.path.join(root, fn), f'{V}/tree3'))
exp_tree = sorted(['CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java',
    'CoreWM/jobs/com/risk/wm/jobs/RiskReportJob.java', 'CoreWM/jobs/LegacyJob.java',
    'Risk_DL/common/com/risk/dl/DbUtil.java', 'Risk_DB/ddl/risk_tables.sql',
    'Risk_DB/pkg/risk_calc_pkg.pkb', 'Risk_DB/sql/post_load.sql',
    'scripts/run_nightly.sh', 'scripts/jar_main.sh'])
exp_tree3 = sorted([f for f in exp_tree if not f.startswith('scripts/')])
check('A', 15, 'full run 6 units / 9-file tree / 3-repo 7-file tree',
      len(units) == 6 and sorted(tree_files) == exp_tree and sorted(tree3_files) == exp_tree3,
      f"units={len(units)} tree={sorted(tree_files)} tree3={sorted(tree3_files)}")

# 16 — pattern learning
pat = load(f'{V}/patterns.json')
pat_prop = load(f'{V}/patterns_prop.json')
def approved(entries):
    return [e for e in entries if e.get('status') == 'approved']
lib_ok = [e for e in approved(pat.get('library_map', [])) if e.get('from') == 'com.risk.db.OracleWriter' and e.get('to') == 'com.bank.cloud.AdlsWriter']
call_ok = [e for e in approved(pat.get('call_map', [])) if e.get('to') is None]
sql_ok = approved(pat.get('sql_overrides', []))
idioms = pat.get('cloud_idioms', {})
check('A', 16, 'pattern learning approve auto',
      len(lib_ok) == 1 and len(call_ok) == 1 and len(sql_ok) == 1
      and 'com.bank.cloud.AdlsWriter' in idioms.get('imports', {})
      and all(e.get('status') == 'proposed' for e in
              pat_prop.get('library_map', []) + pat_prop.get('call_map', []) + pat_prop.get('sql_overrides', [])),
      f"lib={lib_ok} call={call_ok} sql={len(sql_ok)} idioms={idioms} prop_statuses={[e.get('status') for e in pat_prop.get('library_map', []) + pat_prop.get('call_map', []) + pat_prop.get('sql_overrides', [])]}")

# 17 — pattern application
def _entries(bundle):
    return bundle.get('items') or bundle.get('statements') or bundle.get('literals') or []

def _applied_vals(bundle):
    vals = []
    if isinstance(bundle.get('applied'), list):
        vals.append(bundle['applied'])
    for it in _entries(bundle):
        if isinstance(it.get('applied'), list):
            vals.append(it['applied'])
    return vals

def _all_flags(bundle):
    fl = list(bundle.get('flags', []) or [])
    for it in _entries(bundle):
        fl.extend(it.get('flags', []) or [])
    return fl

sw = open(f'{V}/tree-pat/ex/SimpleWriter.java').read()
sw_bundle = load(f'{V}/tree-pat/translation-bundles/ex/SimpleWriter.java.translate.json')
ml_bundle = load(f'{V}/tree-pat/translation-bundles/ex/merge_load.sql.translate.json')
sw_np = open(f'{V}/tree-nopat/ex/SimpleWriter.java').read()
ml_np_bundle = load(f'{V}/tree-nopat/translation-bundles/ex/merge_load.sql.translate.json')
ml_applied = _applied_vals(ml_bundle)
check('A', 17, 'pattern application',
      'com.bank.cloud.AdlsWriter' in sw and 'com.risk.db.OracleWriter' not in sw
      and any('OracleWriter' in str(f.get('from', '')) and f.get('kind') == 'call-site'
              and f.get('lines') == [7] for f in sw_bundle.get('pattern_flags', []))
      and any(f.get('from') == 'commit' and f.get('kind') == 'call-site'
              and f.get('lines') == [9] for f in sw_bundle.get('pattern_flags', []))
      and ['pattern:sql-001'] in ml_applied and _all_flags(ml_bundle) == []
      and 'com.risk.db.OracleWriter' in sw_np
      and 'MERGE: review — Spark MERGE syntax differs in places' in _all_flags(ml_np_bundle),
      f"sw import rewritten={'com.bank.cloud.AdlsWriter' in sw} pattern_flags={sw_bundle.get('pattern_flags')} ml applied={ml_applied} ml flags={_all_flags(ml_bundle)} nopat import unchanged={'com.risk.db.OracleWriter' in sw_np} nopat flags={_all_flags(ml_np_bundle)}")

# 18 — --base merge
pat_base = load(f'{V}/patterns_base.json')
def counts(p):
    return (len(p.get('library_map', [])), len(p.get('call_map', [])), len(p.get('sql_overrides', [])))
check('A', 18, '--base merge keeps entries/counts/approval',
      counts(pat_base) == counts(pat) == (1, 1, 1)
      and all(e.get('status') == 'approved' for e in
              pat_base.get('library_map', []) + pat_base.get('call_map', []) + pat_base.get('sql_overrides', [])),
      f"base counts={counts(pat_base)} orig counts={counts(pat)}")

# 19 — compile_reports
out19 = sorted(os.listdir(f'{V}/output'))
pc = open(f'{V}/output/process-catalog.csv').read().splitlines()[0] if os.path.exists(f'{V}/output/process-catalog.csv') else ''
cen = open(f'{V}/output/oracle-dialect-census.csv').read().splitlines()[0] if os.path.exists(f'{V}/output/oracle-dialect-census.csv') else ''
dbc = open(f'{V}/output/db-object-catalog.csv').read().splitlines()[0] if os.path.exists(f'{V}/output/db-object-catalog.csv') else ''
check('A', 19, 'compile_reports four files + headers',
      set(['process-catalog.csv', 'classification-report.md', 'oracle-dialect-census.csv', 'db-object-catalog.csv']) <= set(out19)
      and pc == 'unit_id,preclassification,verdict,confidence,summary,key_db_objects,java_file_count,repos,linkage'
      and cen == 'feature,occurrences' and dbc == 'object,kind,defined_in,used_by_units',
      f"output={out19} pc={pc!r} census={cen!r} dbcat={dbc!r}")

# 20 — edge-case scan
edge = rec_by_path(edge_java, 'EdgeCaseSql.java')
check('A', 20, 'edge-case scan EdgeCaseSql',
      edge is not None and edge.get('tables_read') == ['MY_FUNC', 'RISK_POSITIONS', 'T1']
      and edge.get('tables_written') == [] and edge.get('procs_called') == ['CALLMYPROC']
      and edge.get('sql_literal_count') == 4 and edge.get('metrics', {}).get('has_main') is False
      and edge.get('sql_sample', [None]*4)[3] == 'SELECT BOOK, EXPOSURE FROM RISK_POSITIONS ? WHERE AS_OF_DATE = SYSDATE'
      and edge.get('table_columns', {}).get('TABLE') == {'read': ['*'], 'write': []},
      f"tables_read={edge.get('tables_read') if edge else None} procs={edge.get('procs_called') if edge else None} count={edge.get('sql_literal_count') if edge else None} sample3={edge.get('sql_sample', [None]*4)[3] if edge else None} TABLE={edge.get('table_columns', {}).get('TABLE') if edge else None}")

# 21 — SVG layers
svg = re.search(r'<svg.*?</svg>', nightly_html, flags=re.S)
svg_txt = svg.group(0) if svg else ''
check('A', 21, 'SVG upstream/downstream + Oracle kinds',
      'FEED_LANDED' in svg_txt and 'MARKET_DATA_READY' in svg_txt
      and 'main:com.risk.wm.jobs.Ris' in svg_txt
      and 'RISK_AUDIT (w_vs)' in svg_txt and 'RISK_CALC_PKG.CALC' in svg_txt and '(call)' in svg_txt
      and 'Oracle kinds: w=write, r=read, w_vs=write_via_script, w_vp=write_via_proc, r_vs=read_via_script, r_vp=read_via_proc, call=call' in nightly_html,
      f"svg len={len(svg_txt)} upstream ok={'FEED_LANDED' in svg_txt} legend={'Oracle kinds:' in nightly_html}")

# 22 — hints
r_hint = ts.translate("SELECT /*+ FULL(t) */ * FROM t")
r_hint_str = ts.translate("SELECT '/*+ FULL(t) */' FROM t")
post_bundle = load(f'{V}/single/migration-tree/translation-bundles/Risk_DB/sql/post_load.sql.translate.json')
r_st = ts.translate("SELECT SYSTIMESTAMP FROM t")
r_sd = ts.translate("SELECT SYSDATE FROM t")
r_both = ts.translate("SELECT SYSDATE, SYSTIMESTAMP FROM t")
post_applied = None
for it in _entries(post_bundle):
    if 'SYSDATE' in str(it.get('original', '')):
        post_applied = it.get('applied')
check('A', 22, 'hint removal + SYSDATE/SYSTIMESTAMP applied names',
      'optimizer hint removed' in r_hint.get('applied', []) and '/*+' not in r_hint.get('translated', '')
      and '/*+' in r_hint_str.get('translated', '')
      and post_applied == ['SYSDATE->current_timestamp()', 'optimizer hint removed']
      and r_st.get('applied') == ['SYSTIMESTAMP->current_timestamp()']
      and r_sd.get('applied') == ['SYSDATE->current_timestamp()']
      and set(r_both.get('applied', [])) == {'SYSDATE->current_timestamp()', 'SYSTIMESTAMP->current_timestamp()'},
      f"hint applied={r_hint.get('applied')} translated={r_hint.get('translated')!r} str hint survives={'/*+' in r_hint_str.get('translated','')} post={post_applied} st={r_st.get('applied')} sd={r_sd.get('applied')} both={r_both.get('applied')}")

# 23 — --base re-approval
p1 = load(f'{V}/p1.json')
p2 = load(f'{V}/p2.json')
check('A', 23, '--base re-approval',
      all(e.get('status') == 'proposed' for e in p1.get('library_map', []) + p1.get('call_map', []) + p1.get('sql_overrides', []))
      and all(e.get('status') == 'approved' for e in p2.get('library_map', []) + p2.get('call_map', []) + p2.get('sql_overrides', []))
      and counts(p2) == (1, 1, 1),
      f"p1={[e.get('status') for e in p1.get('library_map', []) + p1.get('call_map', []) + p1.get('sql_overrides', [])]} p2={[e.get('status') for e in p2.get('library_map', []) + p2.get('call_map', []) + p2.get('sql_overrides', [])]} counts={counts(p2)}")

# 24 — flag-only PL/SQL in tree
report_full = open(f'{V}/tree/translation-report.md').read()
report_ig = open(f'{V}/tree-ig/translation-report.md').read()
pkb_bundle = load(f'{V}/tree/translation-bundles/Risk_DB/pkg/risk_calc_pkg.pkb.translate.json')
pkb_head = open(f'{V}/tree/Risk_DB/pkg/risk_calc_pkg.pkb').read().split('\n')[:12]
pkb_head_ig = open(f'{V}/tree-ig/Risk_DB/pkg/risk_calc_pkg.pkb').read().split('\n')[:12]
pkb_flags = set()
for it in _entries(pkb_bundle):
    pkb_flags.update(it.get('flags', []))
pkb_noop = [it for it in _entries(pkb_bundle)
            if it.get('applied') == [] and it.get('flags') == [] and it.get('confidence') == 'high']
pkb_flagged = [it for it in _entries(pkb_bundle) if it.get('confidence') == 'needs-review']
check('A', 24, 'flag-only PL/SQL in tree',
      'NightlyRiskJob.java' in report_full and 'risk_calc_pkg.pkb' in report_full
      and any('EXECUTE IMMEDIATE' in f for f in pkb_flags) and any('DBMS_' in f for f in pkb_flags)
      and any('2 flag(s)' in ln for ln in pkb_head)
      and any('1 flag(s)' in ln for ln in pkb_head_ig)
      and len(pkb_bundle.get('statements', [])) == 6 and len(pkb_flagged) == 2 and len(pkb_noop) == 4,
      f"report flagged files ok={('NightlyRiskJob.java' in report_full and 'risk_calc_pkg.pkb' in report_full)} pkb flags={sorted(pkb_flags)} head={pkb_head[:4]} ig head={pkb_head_ig[:4]} items={len(_entries(pkb_bundle))} flagged={len(pkb_flagged)} noop={len(pkb_noop)}")

# 25
tf = rec_by_path(edge_plsql, 'table_func.sql')
check('A', 25, 'TABLE( in PL/SQL',
      tf is not None and tf.get('reads') == ['MY_FUNC', 'T2'],
      f"reads={tf.get('reads') if tf else None}")

# 26
tree_only_files = [f for _, _, fs in os.walk(f'{V}/tree-only-empty') for f in fs]
check('A', 26, '--only without --units/--graph/--catalog',
      p26.returncode == 0 and os.path.isdir(f'{V}/tree-only-empty')
      and tree_only_files == ['translation-report.md']
      and 'Files mirrored: 0' in open(f'{V}/tree-only-empty/translation-report.md').read(),
      f"rc={p26.returncode} files={tree_only_files}")

# 27
s_html = open(f'{V}/single/output/process-docs/NIGHTLY_RISK_JOB.html').read()
s_svg = re.search(r'<svg.*?</svg>', s_html, flags=re.S)
s_svg_txt = s_svg.group(0) if s_svg else ''
check('A', 27, 'SVG in --only mode: no Downstream layer',
      'Downstream' not in s_svg_txt and s_unit is not None
      and s_unit.get('likely_required_by') == [],
      f"Downstream in svg={'Downstream' in s_svg_txt} likely_required_by={s_unit.get('likely_required_by') if s_unit else None}")

# 28 — multi-statement override
ml_body = open(f'{V}/tree-pat/ex/multi_load.sql').read()
mlb = load(f'{V}/tree-pat/translation-bundles/ex/multi_load.sql.translate.json')
ml_items = _entries(mlb)
check('A', 28, 'multi-statement PL/SQL override in body',
      'ON r.BOOK = s.BOOK' in ml_body and 'UPDATE SET TOTAL_EXPOSURE = s.TOTAL_EXPOSURE' in ml_body
      and 'ON (r.BOOK = s.BOOK)' not in ml_body and 'UPDATE SET r.TOTAL_EXPOSURE' not in ml_body
      and ml_items and ml_items[0].get('translated', '').strip() == 'SELECT current_timestamp();'
      and mlb.get('patterns_applied') == ['sql-001']
      and len(ml_items) == 2 and all(i.get('confidence') == 'high' for i in ml_items)
      and all(i.get('flags') == [] for i in ml_items),
      f"body has spark merge={('ON r.BOOK = s.BOOK' in ml_body)} first={ml_items[0].get('translated') if ml_items else None} patterns_applied={mlb.get('patterns_applied')} items={len(ml_items)}")

# 29 — import order
io_ig = rec_by_path(edge_java_ig, 'ImportOrder.java')
io = rec_by_path(edge_java, 'ImportOrder.java')
check('A', 29, 'import order under --ignore',
      io_ig is not None and io_ig.get('imports') == ['zebra.Z', 'alpha.A', 'middle.M']
      and io_ig.get('ignored_imports') == ['oracle.jdbc.OracleDriver']
      and io is not None and io.get('imports') == ['zebra.Z', 'alpha.A', 'oracle.jdbc.OracleDriver', 'middle.M'],
      f"ig imports={io_ig.get('imports') if io_ig else None} ignored={io_ig.get('ignored_imports') if io_ig else None} plain={io.get('imports') if io else None}")

# 30 — FROM-table scan
ft_j = rec_by_path(edge_java, 'FromTables.java')
ft_p = rec_by_path(edge_plsql, 'from_tables.sql')
exp_tc30 = {'T1': {'read': ['D'], 'write': []}, 'T2': {'read': ['C'], 'write': []}}
check('A', 30, 'FROM-table scan both scanners',
      ft_j is not None and ft_j.get('tables_read') == ['T1', 'T2']
      and ft_j.get('table_columns') == exp_tc30 and ft_j.get('columns_unattributed') == ['A', 'B']
      and ft_p is not None and ft_p.get('reads') == ['T1', 'T2']
      and ft_p.get('table_columns') == exp_tc30 and ft_p.get('columns_unattributed') == ['A', 'B'],
      f"java={ft_j.get('tables_read') if ft_j else None}/{ft_j.get('table_columns') if ft_j else None}/{ft_j.get('columns_unattributed') if ft_j else None} plsql={ft_p.get('reads') if ft_p else None}/{ft_p.get('table_columns') if ft_p else None}/{ft_p.get('columns_unattributed') if ft_p else None}")

# 31 — determinism
det_ok = len(set(det_md5.values())) == 1
nj_files = [f['path'] for f in nightly_unit.get('java_files', [])]
check('A', 31, 'determinism PYTHONHASHSEED 0-7',
      det_ok and nj_files == sorted(nj_files)
      and nj_files[0].endswith('NightlyRiskJob.java') and nj_files[-1].endswith('DbUtil.java'),
      f"md5s={sorted(set(det_md5.values()))} java_files={nj_files}")

# 32 — Java override
mw_bundle = load(f'{V}/tree-pat/translation-bundles/ex/MergeWriter.java.translate.json')
mw_body = open(f'{V}/tree-pat/ex/MergeWriter.java').read()
mw_items = [i for i in _entries(mw_bundle) if i.get('applied') == ['pattern:sql-001']]
check('A', 32, 'Java override match',
      len(mw_items) == 1 and mw_items[0].get('confidence') == 'high' and mw_items[0].get('flags') == []
      and 'ON r.BOOK = s.BOOK' in mw_body,
      f"items applied={mw_items[0].get('applied') if mw_items else None} conf={mw_items[0].get('confidence') if mw_items else None} body has spark merge={'ON r.BOOK = s.BOOK' in mw_body}")

# 33 — commented override
cm_body = open(f'{V}/tree-pat/ex/commented_merge.sql').read()
cm_bundle = load(f'{V}/tree-pat/translation-bundles/ex/commented_merge.sql.translate.json')
check('A', 33, 'commented override',
      '-- a comment before the merge' in cm_body
      and cm_body.index('-- a comment before the merge') < cm_body.index('MERGE INTO')
      and 'ON r.BOOK = s.BOOK' in cm_body
      and any(i.get('applied') == ['pattern:sql-001'] for i in _entries(cm_bundle))
      and _all_flags(cm_bundle) == [],
      f"comment before merge={('-- a comment before the merge' in cm_body)} spark={('ON r.BOOK = s.BOOK' in cm_body)} flags={_all_flags(cm_bundle)}")

# 34 — terminator preserved
pat_nosemi = load(f'{V}/patterns_nosemi.json')
sql001_ns = next((e for e in pat_nosemi.get('sql_overrides', []) if e.get('id') == 'sql-001' or True), None)
to_ns = (sql001_ns or {}).get('to', '')
ns_ml = open(f'{V}/tree-nosemi/ex/multi_load.sql').read()
ns_ns = open(f'{V}/tree-nosemi/ex/nosemi_load.sql').read()
ns_bundle = load(f'{V}/tree-nosemi/translation-bundles/ex/nosemi_load.sql.translate.json')
check('A', 34, 'terminator preserved (nosemi)',
      not to_ns.rstrip().endswith(';')
      and 'VALUES (s.BOOK, s.TOTAL_EXPOSURE);' in ns_ml
      and 'VALUES (s.BOOK, s.TOTAL_EXPOSURE);' in ns_ns
      and any(i.get('applied') == ['pattern:sql-001'] for i in _entries(ns_bundle)),
      f"learned to ends with ;={to_ns.rstrip().endswith(';')} multi has ;={('VALUES (s.BOOK, s.TOTAL_EXPOSURE);' in ns_ml)} nosemi has ;={('VALUES (s.BOOK, s.TOTAL_EXPOSURE);' in ns_ns)}")

# 35-38 — edge-case records
def edge_pair(java_name, sql_name):
    return rec_by_path(edge_java, java_name), rec_by_path(edge_plsql, sql_name)

ct_j, ct_p = edge_pair('CommaTables.java', 'comma_tables.sql')
check('A', 35, 'comma-only tables',
      ct_j is not None and ct_j.get('tables_read') == ['T1', 'T2'] and ct_j.get('table_columns') == {}
      and ct_j.get('columns_unattributed') == ['A', 'B']
      and ct_p is not None and ct_p.get('reads') == ['T1', 'T2'] and ct_p.get('table_columns') == {}
      and ct_p.get('columns_unattributed') == ['A', 'B'],
      f"java={ct_j.get('tables_read') if ct_j else None}/{ct_j.get('table_columns') if ct_j else None}/{ct_j.get('columns_unattributed') if ct_j else None} plsql={ct_p.get('reads') if ct_p else None}")

dt_j, dt_p = edge_pair('DerivedTables.java', 'derived_tables.sql')
exp_tc36 = {'T1': {'read': ['A', 'B'], 'write': []}, 'T2': {'read': ['A'], 'write': []}}
check('A', 36, 'derived tables',
      dt_j is not None and dt_j.get('tables_read') == ['T1', 'T2'] and dt_j.get('table_columns') == exp_tc36
      and dt_j.get('columns_unattributed') == []
      and dt_p is not None and dt_p.get('reads') == ['T1', 'T2'] and dt_p.get('table_columns') == exp_tc36
      and dt_p.get('columns_unattributed') == [],
      f"java={dt_j.get('table_columns') if dt_j else None} plsql={dt_p.get('table_columns') if dt_p else None}")

qs_j, qs_p = edge_pair('QualifiedStar.java', 'qualified_star.sql')
exp_tc37 = {'T1': {'read': ['*'], 'write': []}, 'T2': {'read': ['C'], 'write': []}}
check('A', 37, 'qualified star',
      qs_j is not None and qs_j.get('tables_read') == ['T1', 'T2'] and qs_j.get('table_columns') == exp_tc37
      and qs_j.get('columns_unattributed') == []
      and qs_p is not None and qs_p.get('reads') == ['T1', 'T2'] and qs_p.get('table_columns') == exp_tc37
      and qs_p.get('columns_unattributed') == [],
      f"java={qs_j.get('table_columns') if qs_j else None} plsql={qs_p.get('table_columns') if qs_p else None}")

sr_j, sr_p = edge_pair('StarRule.java', 'star_rule.sql')
uc_j, uc_p = edge_pair('UnionColumns.java', 'union_columns.sql')
exp_sr = {'T1': {'read': ['*', 'A'], 'write': []}, 'T2': {'read': ['A', 'Y'], 'write': []}, 'T3': {'read': ['Y'], 'write': []}}
exp_uc = {'T1': {'read': ['A'], 'write': []}, 'T2': {'read': ['B'], 'write': []}}
check('A', 38, '* rule + UNION columns',
      sr_j is not None and sr_j.get('tables_read') == ['T1', 'T2', 'T3'] and sr_j.get('table_columns') == exp_sr
      and sr_j.get('columns_unattributed') == []
      and sr_p is not None and sr_p.get('reads') == ['T1', 'T2', 'T3'] and sr_p.get('table_columns') == exp_sr
      and uc_j is not None and uc_j.get('tables_read') == ['T1', 'T2'] and uc_j.get('table_columns') == exp_uc
      and uc_j.get('columns_unattributed') == []
      and uc_p is not None and uc_p.get('reads') == ['T1', 'T2'] and uc_p.get('table_columns') == exp_uc,
      f"StarRule java={sr_j.get('table_columns') if sr_j else None} plsql={sr_p.get('table_columns') if sr_p else None} UnionColumns java={uc_j.get('table_columns') if uc_j else None}")

# 39 — ArgsJoin
aj = rec_by_path(edge_java, 'ArgsJoin.java')
aj_bundle = load(f'{V}/tree-args/translation-bundles/edge/ArgsJoin.java.translate.json')
aj_body = open(f'{V}/tree-args/edge/ArgsJoin.java').read()
aj_samples = aj.get('sql_sample', []) if aj else []
aj_rn_items = [i for i in _entries(aj_bundle)
               if any('ROWNUM' in f for f in i.get('flags', []))]
aj_sys_items = [i for i in _entries(aj_bundle)
                if i.get('applied') == ['FROM DUAL removed', 'SYSDATE->current_timestamp()']]
check('A', 39, 'ArgsJoin tree',
      aj is not None and aj.get('sql_literal_count') == 3
      and aj.get('oracle_isms', {}).get('ROWNUM') == 2
      and sum(1 for s in aj_samples if s == 'SELECT a FROM t ? WHERE ROWNUM <= 5') == 2
      and len(aj_rn_items) >= 2
      and all(i.get('confidence') == 'needs-review' for i in aj_rn_items)
      and all(i.get('chain_sql') == 'SELECT a FROM t  ?  WHERE ROWNUM <= 5' for i in aj_rn_items)
      and aj_body.count('TODO-SPARK: ROWNUM: rewrite') == 2
      and len(aj_sys_items) == 1
      and '""' in aj_body,
      f"literals={aj.get('sql_literal_count') if aj else None} ROWNUM={aj.get('oracle_isms') if aj else None} samples={aj_samples} rn_items={len(aj_rn_items)} markers={aj_body.count('/* TODO-SPARK: ROWNUM')} sys_items={len(aj_sys_items)}")

# 40 — commented pair round-trip
pat_c = load(f'{V}/patterns_commented.json')
sql001_c = next((e for e in pat_c.get('sql_overrides', [])) , None)
cm2_body = open(f'{V}/tree-commented/ex/commented_merge.sql').read()
cm2_bundle = load(f'{V}/tree-commented/translation-bundles/ex/commented_merge.sql.translate.json')
check('A', 40, 'commented-pair round-trip',
      sql001_c is not None and sql001_c.get('status') == 'approved'
      and (sql001_c.get('from', '') or '').startswith('MERGE INTO RISK_RESULTS r')
      and cm2_body.lstrip().startswith('/* MIGRATION FIRST PASS')
      and '-- a comment before the merge' in cm2_body
      and cm2_body.index('-- a comment before the merge') < cm2_body.index('MERGE INTO')
      and 'ON r.BOOK = s.BOOK' in cm2_body
      and all(i.get('applied') == ['pattern:sql-001'] for i in _entries(cm2_bundle))
      and cm2_bundle.get('patterns_applied') == ['sql-001']
      and _all_flags(cm2_bundle) == [],
      f"from head={(sql001_c.get('from', '')[:40] if sql001_c else None)!r} status={sql001_c.get('status') if sql001_c else None} patterns_applied={cm2_bundle.get('patterns_applied')} flags={_all_flags(cm2_bundle)}")

# 41 — Limits parenthetical
check('A', 41, 'Limits parenthetical in both reports',
      'Mechanical rewrites split across concatenated fragments' in report_full
      and 'Mechanical rewrites split across concatenated fragments' in open(f'{V}/single/migration-tree/translation-report.md').read(),
      f"full={('Mechanical rewrites split across concatenated fragments' in report_full)} single={('Mechanical rewrites split across concatenated fragments' in open(f'{V}/single/migration-tree/translation-report.md').read())}")

# runnable guard (R7-G2)
import scan_java as sj
import prepare_migration_tree as pmt
check('A', 'G', 'BETWEEN_RE guard runnable + identical',
      pmt.BETWEEN_RE.pattern == sj.BETWEEN_RE.pattern
      and pmt.BETWEEN_RE is sj.BETWEEN_RE,
      f"identical pattern={pmt.BETWEEN_RE.pattern == sj.BETWEEN_RE.pattern} same object={pmt.BETWEEN_RE is sj.BETWEEN_RE}")

# 42 — wrapped-only synthetic coverage
w_ids = sorted([u['unit_id'] for u in units_wrapped])
check('A', 42, 'script-only synthetic coverage',
      w_ids == ['WRAPPED_JOB', 'main:LegacyJob', 'main:com.risk.wm.jobs.RiskReportJob'],
      f"units={w_ids}")

# 43 — output-tables * rule
sn_j, sn_p = edge_pair('StarRuleNested.java', 'star_rule_nested.sql')
exp_tc43 = {'T1': {'read': ['*', 'A'], 'write': []}, 'T2': {'read': ['C', 'Y'], 'write': []}}
check('A', 43, 'output-tables * rule',
      sn_j is not None and sn_j.get('tables_read') == ['T1', 'T2'] and sn_j.get('table_columns') == exp_tc43
      and sn_j.get('columns_unattributed') == []
      and sn_p is not None and sn_p.get('reads') == ['T1', 'T2'] and sn_p.get('table_columns') == exp_tc43
      and sn_p.get('columns_unattributed') == [],
      f"java={sn_j.get('table_columns') if sn_j else None} plsql={sn_p.get('table_columns') if sn_p else None}")

# 44 — DISTINCT
ds_j, ds_p = edge_pair('DistinctSelect.java', 'distinct_select.sql')
exp_tc44 = {'T1': {'read': ['A', 'B'], 'write': []}}
check('A', 44, 'DISTINCT/ALL select lists',
      ds_j is not None and ds_j.get('tables_read') == ['T1'] and ds_j.get('table_columns') == exp_tc44
      and ds_j.get('columns_unattributed') == []
      and ds_p is not None and ds_p.get('reads') == ['T1'] and ds_p.get('table_columns') == exp_tc44
      and ds_p.get('columns_unattributed') == [],
      f"java={ds_j.get('table_columns') if ds_j else None} plsql={ds_p.get('table_columns') if ds_p else None}")

# 45 — terminator reverse direction
pat_semi = load(f'{V}/patterns_semi.json')
semi_body = open(f'{V}/tree-semi/ex/semi_load.sql').read()
semi_bundle = load(f'{V}/tree-semi/translation-bundles/ex/semi_load.sql.translate.json')
check('A', 45, 'terminator preserved reverse direction',
      not semi_body.strip().endswith(';')
      and 'UPDATE SET TOTAL_EXPOSURE = s.TOTAL_EXPOSURE' in semi_body
      and any(i.get('applied') == ['pattern:sql-001'] for i in _entries(semi_bundle)),
      f"body ends with ;={semi_body.strip().endswith(';')} tail={semi_body.strip()[-80:]!r}")

# 46 — three-part columns
tp_j, tp_p = edge_pair('ThreePartColumns.java', 'three_part_columns.sql')
exp_tc46 = {'SCHEMA1.T1': {'read': ['A', 'B'], 'write': []}}
check('A', 46, 'three-part column refs',
      tp_j is not None and tp_j.get('tables_read') == ['SCHEMA1.T1', 'T1']
      and tp_j.get('table_columns') == exp_tc46 and tp_j.get('columns_unattributed') == ['FOO.BAR.A']
      and tp_p is not None and tp_p.get('reads') == ['SCHEMA1.T1', 'T1']
      and tp_p.get('table_columns') == exp_tc46 and tp_p.get('columns_unattributed') == ['FOO.BAR.A'],
      f"java={tp_j.get('tables_read') if tp_j else None}/{tp_j.get('table_columns') if tp_j else None}/{tp_j.get('columns_unattributed') if tp_j else None} plsql={tp_p.get('reads') if tp_p else None}")

# ================= flagged item: WithSelectSubquery (R7-G8) =================
ws_j, ws_p = edge_pair('WithSelectSubquery.java', 'with_select_subquery.sql')
exp_ws_tc = {'T1': {'read': ['A'], 'write': []}, 'T2': {'read': ['A', 'B'], 'write': []}}
ws_ok = (ws_j is not None and ws_j.get('tables_read') == ['T1', 'T2']
         and ws_j.get('table_columns') == exp_ws_tc and ws_j.get('columns_unattributed') == []
         and 'C' not in ws_j.get('table_columns', {})
         and ws_p is not None and ws_p.get('reads') == ['T1', 'T2']
         and ws_p.get('table_columns') == exp_ws_tc and ws_p.get('columns_unattributed') == []
         and 'C' not in ws_p.get('table_columns', {}))
# bare shape alone, probed directly: expect T2:{A} only (the documented discrepancy)
def _norm_tc(tc):
    return {t: {'read': sorted(v.get('read', [])), 'write': sorted(v.get('write', []))}
            for t, v in tc.items()}
bare_j = sj.extract_table_columns('SELECT (WITH c AS (SELECT a FROM t2) SELECT a FROM c) FROM t1')
import scan_plsql as sp
bare_p = sp.extract_table_columns('SELECT (WITH c AS (SELECT a FROM t2) SELECT a FROM c) FROM t1;')
fixture_text = ' '.join(open(os.path.join(KIT, 'fixtures/edge-cases/WithSelectSubquery.java')).read().split())
fixture_note = ('does not' in fixture_text and 'reproduce' in fixture_text
                and 'T2:{A} only' in fixture_text)
check('F', 'WS', 'WithSelectSubquery: no C leak + bare shape T2:{A} only + fixture documents discrepancy',
      ws_ok and _norm_tc(bare_j[0]) == {'T2': {'read': ['A'], 'write': []}} and sorted(bare_j[1]) == []
      and _norm_tc(bare_p[0]) == {'T2': {'read': ['A'], 'write': []}} and sorted(bare_p[1]) == []
      and fixture_note,
      f"java={ws_j.get('table_columns') if ws_j else None} plsql={ws_p.get('table_columns') if ws_p else None} bare_java={bare_j} bare_plsql={bare_p} fixture_note={fixture_note}")

# ================= mechanism-diff checklist (derived from section 4) =================
import importlib

def _src(modname):
    import inspect
    return inspect.getsource(importlib.import_module(modname))

# M1 — BETWEEN_RE: one shared constant, guard runnable
tree_src = open(os.path.join(KIT, 'scripts/prepare_migration_tree.py')).read()
java_src = open(os.path.join(KIT, 'scripts/scan_java.py')).read()
check('M', 'M1', 'BETWEEN_RE single shared constant',
      java_src.count('BETWEEN_RE = ') == 1
      and 'from scan_java import' in tree_src and 'BETWEEN_RE' in tree_src
      and 'between = re.compile' not in tree_src
      and pmt.BETWEEN_RE.pattern == sj.BETWEEN_RE.pattern and pmt.BETWEEN_RE is sj.BETWEEN_RE,
      f"defs in scan_java={java_src.count('BETWEEN_RE = ')} tree imports BETWEEN_RE={'from scan_java import' in tree_src} forked literal={'between = re.compile' in tree_src} same object={pmt.BETWEEN_RE is sj.BETWEEN_RE}")

# M2 — TABLE skip mechanism
ft = sj._from_tables('FROM TABLE(my_func)')
rt = sj._read_tables('SELECT * FROM TABLE(my_func)')
rt_p = sp._read_tables('SELECT * FROM TABLE(my_func)')
tf_re_j = sj._TABLE_FUNC_RE.search('FROM TABLE(my_func)')
tf_re_p = sp._TABLE_FUNC_RE.search('FROM TABLE(my_func)')
tc_j = _norm_tc(sj.extract_table_columns('SELECT * FROM TABLE(my_func)')[0])
tc_p = _norm_tc(sp.extract_table_columns('SELECT * FROM TABLE(my_func);')[0])
check('M', 'M2', 'TABLE skip mechanism',
      '(?!TABLE' not in java_src and '(?!TABLE' not in open(os.path.join(KIT, 'scripts/scan_plsql.py')).read()
      and ('TABLE', None) in ft and rt == {'MY_FUNC'} and rt_p == {'MY_FUNC'}
      and tf_re_j and tf_re_j.group(1) == 'my_func' and tf_re_p and tf_re_p.group(1) == 'my_func'
      and tc_j == {'TABLE': {'read': ['*'], 'write': []}} and tc_p == {'TABLE': {'read': ['*'], 'write': []}},
      f"lookahead absent={('(?!TABLE' not in java_src)} _from_tables={ft} _read_tables={rt}/{rt_p} func_re={tf_re_j.group(1) if tf_re_j else None} tc={tc_j}/{tc_p}")

# M3 — covered_fqns script BFS (code) + A42 behavior
bg_src = open(os.path.join(KIT, 'scripts/build_graph.py')).read()
check('M', 'M3', 'covered_fqns script-mediated BFS in code',
      'covered_fqns' in bg_src and 'script_java' in bg_src
      and 'BFS' in bg_src.split('covered_fqns', 1)[1][:1500],
      "BFS each job's scripts following script->script before synthetic creation (see build_graph.py covered_fqns block); behavior confirmed by A42 (3 units, no synthetic nightly)")

# M4 — _split_leading_comments shared, never forked
lp = importlib.import_module('learn_patterns')
def _shared(fn, mod):
    return any(v is fn for v in vars(mod).values())
check('M', 'M4', '_split_leading_comments single shared helper',
      open(os.path.join(KIT, 'scripts/translate_sql.py')).read().count('def _split_leading_comments') == 1
      and _shared(ts._split_leading_comments, pmt)
      and _shared(ts._split_leading_comments, lp),
      f"defined once in translate_sql={open(os.path.join(KIT, 'scripts/translate_sql.py')).read().count('def _split_leading_comments')} shared by tree={_shared(ts._split_leading_comments, pmt)} shared by learn={_shared(ts._split_leading_comments, lp)}")

# M5 — -jar terminal gate in both scanners
from scan_jobs import dissect_command
jm = rec_by_path(load(f'{V}/scripts_fixtures.json'), 'jar_main.sh')
ss_src = open(os.path.join(KIT, 'scripts/scan_scripts.py')).read()
check('M', 'M5', '-jar terminal gate in scan_jobs + scan_scripts',
      dissect_command('java -jar app.jar com.foo.Bar').get('main_class') is None
      and jm is not None and jm.get('java_mains') == ['com.foo.Real']
      and jm.get('jars') == ['app.jar', 'lib.jar']
      and (r'(?<![\w-])-jar\b' in ss_src or '-jar' in ss_src and 'R7-G9' in ss_src),
      f"dissect jar={dissect_command('java -jar app.jar com.foo.Bar')} script mains={jm.get('java_mains') if jm else None} jars={jm.get('jars') if jm else None}")

# M6 — DUAL skip, both scanners, both places (end-to-end temp probes)
os.makedirs(f'{V}/probe', exist_ok=True)
with open(f'{V}/probe/DualProbe.java', 'w') as f:
    f.write('package p;\npublic class DualProbe {\n public void run(java.sql.Statement st) throws Exception {\n'
            '  st.executeQuery("SELECT * FROM DUAL");\n'
            '  st.executeQuery("SELECT SYSDATE FROM DUAL");\n'
            '  st.executeQuery("SELECT * FROM SYS.DUAL");\n'
            '  st.executeQuery("SELECT d FROM DUAL");\n'
            '  st.executeQuery("INSERT INTO DUAL (a) VALUES (1)");\n'
            '  st.executeQuery("UPDATE DUAL SET a = 1");\n }\n}\n')
with open(f'{V}/probe/dual_probe.sql', 'w') as f:
    f.write('SELECT * FROM DUAL;\nSELECT SYSDATE FROM DUAL;\nSELECT * FROM SYS.DUAL;\nSELECT d FROM DUAL;\nINSERT INTO DUAL (a) VALUES (1);\nUPDATE DUAL SET a = 1;\n')
run([PY, f'{S}/scan_java.py', '--root', f'{V}/probe', '--repo', 'Probe', '--out', f'{V}/probe/dual_java.json'])
run([PY, f'{S}/scan_plsql.py', '--root', f'{V}/probe', '--repo', 'Probe', '--exts', '.sql', '--out', f'{V}/probe/dual_plsql.json'])
dj = load(f'{V}/probe/dual_java.json')
dp = load(f'{V}/probe/dual_plsql.json')
dj_rec = dj[0] if dj else {}
dp_rec = dp[0] if dp else {}
check('M', 'M6', 'DUAL skip both scanners both places',
      dj_rec.get('tables_read') == [] and dj_rec.get('table_columns') == {}
      and dj_rec.get('columns_unattributed') == []
      and dp_rec.get('reads') == [] and dp_rec.get('table_columns') == {}
      and dp_rec.get('columns_unattributed') == []
      and dj_rec.get('oracle_isms', {}).get('DUAL', 0) >= 1,
      f"java reads={dj_rec.get('tables_read')} tc={dj_rec.get('table_columns')} unatt={dj_rec.get('columns_unattributed')} isms={dj_rec.get('oracle_isms')} plsql reads={dp_rec.get('reads')} tc={dp_rec.get('table_columns')} unatt={dp_rec.get('columns_unattributed')}")

# M7 — java_files sorted by path (code + A31 behavior)
check('M', 'M7', 'java_files sorted by path in code',
      'sorted(' in bg_src and 'java_files' in bg_src
      and nj_files == sorted(nj_files),
      f"sorted java_files in build_graph + A31 md5-identical across seeds 0-7: {det_ok}")

# M8 — auto_approves scoped by list (code + behavioral probe)
lp_src = open(os.path.join(KIT, 'scripts/learn_patterns.py')).read()
os.makedirs(f'{V}/probe_migrated', exist_ok=True)
base_probe = {
    'version': 1,
    'library_map': [{'from': 'com.a.A', 'to': 'com.b.B', 'status': 'proposed',
                     'evidence': ['pair-1', 'pair-2', 'pair-3'], 'count': 3,
                     'unambiguous': False, 'vetted': False}],
    'call_map': [{'from': 'commit', 'to': None, 'status': 'proposed',
                  'evidence': ['pair-1', 'pair-2', 'pair-3'], 'count': 3,
                  'unambiguous': False, 'vetted': False}],
    'sql_overrides': [{'id': 'sql-001', 'from': 'SELECT a FROM t1', 'to': 'SELECT a FROM t1',
                       'status': 'proposed', 'evidence': ['pair-1', 'pair-2', 'pair-3'],
                       'count': 3, 'unambiguous': False, 'vetted': False}],
    'cloud_idioms': {}, 'generated_from': {'pairs': [], 'migrated_dirs': []}, 'warnings': [],
}
with open(f'{V}/probe_base.json', 'w') as f:
    json.dump(base_probe, f)
run([PY, f'{S}/learn_patterns.py', '--migrated-dir', f'{V}/probe_migrated',
     '--base', f'{V}/probe_base.json', '--approve', 'auto', '--out', f'{V}/probe_out.json'])
po = load(f'{V}/probe_out.json')
po_status = [e.get('status') for e in
             po.get('library_map', []) + po.get('call_map', []) + po.get('sql_overrides', [])]
check('M', 'M8', 'auto_approves count>=3 scoped to library/SQL; non-matching base entries not re-evaluated',
      "kind in ('library_map', 'sql_overrides')" in lp_src
      and po_status == ['proposed', 'proposed', 'proposed'],
      f"gate in code={'kind' in lp_src and 'library_map' in lp_src} probe statuses={po_status}")

# M9 — PKG.MEMBER defined_in
cat_entry = next((c for c in catalog if c.get('object') == 'RISK_CALC_PKG.CALC_EXPOSURE'), None)
check('M', 'M9', 'PKG.MEMBER defined_in from owning package',
      cat_entry is not None and cat_entry.get('kind') == 'PROCEDURE'
      and cat_entry.get('defined_in') == ['fixtures/Risk_DB/pkg/risk_calc_pkg.pkb'],
      f"entry={cat_entry}")

# M10a — DISTINCT strip + _SQL_KW (code + A44 behavior)
kw_ok = ("'UNIQUE'" in java_src or '"UNIQUE"' in java_src) and 'DISTINCTROW' in java_src
plsql_src = open(os.path.join(KIT, 'scripts/scan_plsql.py')).read()
kw_ok_p = ("'UNIQUE'" in plsql_src or '"UNIQUE"' in plsql_src) and 'DISTINCTROW' in plsql_src
check('M', 'M10a', 'DISTINCT/ALL/UNIQUE strip + _SQL_KW entries',
      kw_ok and kw_ok_p and 'DISTINCT' in java_src,
      f"java _SQL_KW has UNIQUE/DISTINCTROW={kw_ok} plsql={kw_ok_p}")

# M10b — _strip_select_subqueries matches (SELECT|WITH)
check('M', 'M10b', '_strip_select_subqueries covers SELECT|WITH',
      r'(?:SELECT|WITH)' in java_src and r'(?:SELECT|WITH)' in plsql_src,
      f"pattern present in both scanners; behavior confirmed by F/WS (no C leak)")

# M10c — apply_ignore filters columns_unattributed
import build_graph as bg
try:
    jr = [{'path': 'x.java', 'repo': 'R', 'imports': ['oracle.jdbc.OracleDriver', 'com.a.B'],
           'tables_read': ['V$SESSION', 'T1'], 'tables_written': [], 'procs_called': [],
           'table_columns': {}, 'columns_unattributed': ['V$SESSION.CNT', 'DBMS_OUTPUT.X', 'PLAIN'],
           'ignored_imports': [], 'ignored_tables': [], 'ignored_table_columns': {}}]
    pr = [{'path': 'x.sql', 'repo': 'R', 'reads': ['V$SESSION', 'T1'], 'writes': [], 'calls': [],
           'references': [], 'table_columns': {}, 'columns_unattributed': ['V$SESSION.CNT', 'PLAIN'],
           'ignored_calls': [], 'ignored_tables': []}]
    jr2, pr2 = bg.apply_ignore([dict(r) for r in jr], [dict(r) for r in pr], ign)
    m10c_ok = (jr2[0].get('columns_unattributed') == ['PLAIN']
               and pr2[0].get('columns_unattributed') == ['PLAIN'])
    m10c_ev = f"java unatt={jr2[0].get('columns_unattributed')} plsql unatt={pr2[0].get('columns_unattributed')}"
except Exception as e:  # noqa
    m10c_ok, m10c_ev = False, f'exception: {e!r}'
check('M', 'M10c', 'apply_ignore filters columns_unattributed',
      m10c_ok, m10c_ev)

# M10d — unit_scripts BFS over script->script chains
import build_process_docs as bpd
out_e = {'job:U1': [{'dst': 'script:s1.sh'}],
         'script:s1.sh': [{'dst': 'script:s2.sh'}],
         'script:s2.sh': [{'dst': 'java:com.x.Job'}]}
in_e = {'java:com.x.Job': [{'src': 'script:s2.sh'}]}
us = bpd.unit_scripts('job:U1', out_e, in_e)
check('M', 'M10d', 'unit_scripts BFS follows script->script chains',
      us == ['script:s1.sh', 'script:s2.sh'], f"unit_scripts={us}")

# M10e — terminator rule both directions (code + A34/A45 behavior)
tree_src2 = tree_src
check('M', 'M10e', 'terminator rule: compare strips ;, re-append/strip in tree',
      '_strip_terminator' in open(os.path.join(KIT, 'scripts/translate_sql.py')).read()
      and 're-append' in tree_src2
      and "translated.endswith(';')" in tree_src2,
      "compare strips one trailing ; on both sides (translate_sql._strip_terminator); tree re-appends when original has it and strips when original lacks it; behavior confirmed by A34 (gains ;) and A45 (no ; gained)")

# M10f — CTE resolution
cte_j = sj._read_tables('WITH cte AS (SELECT a FROM t1) SELECT b FROM cte')
cte_p = sp._read_tables('WITH cte AS (SELECT a FROM t1) SELECT b FROM cte')
cte_tc = _norm_tc(sj.extract_table_columns('WITH cte AS (SELECT a FROM t1) SELECT b FROM cte')[0])
check('M', 'M10f', 'CTE names never reported as tables',
      cte_j == {'T1'} and cte_p == {'T1'} and cte_tc == {'T1': {'read': ['A', 'B'], 'write': []}},
      f"reads={cte_j}/{cte_p} tc={cte_tc}")

# M11 — shipped zip + prompt match the stated md5s and the kit on disk.
# Portable: verifies whichever shipped artifacts are found next to the checker
# (or via $MIGRATION_KIT_ZIP / $MIGRATION_KIT_PROMPT) and reports any it could
# not find, instead of failing on a path that only exists in the build VM.
# Expected md5s (final kit, 2026-11-18): zip bef571e6aac8e80a7dbc137ad6c0c0c1,
# prompt 7b8937fd4eb7a9ff95fc1fe144b3b18e; zip holds 97 entries / 71 files.
import zipfile
zip_path = _first_existing(ZIP_CANDIDATES)
found = []
skipped = []
disk = []
for root, dirs, files in os.walk(KIT):
    dirs[:] = [d for d in dirs if d != '__pycache__']
    for fn in files:
        if fn in SELFTEST_FILES and os.path.dirname(os.path.join(root, fn)) == KIT:
            continue  # this self-test's own files are not part of the shipped kit
        disk.append(os.path.relpath(os.path.join(root, fn), KIT))
zip_ok = True
names = []
if zip_path:
    z = zipfile.ZipFile(zip_path)
    names = [n[len('migration-kit/'):] for n in z.namelist()
             if n != 'migration-kit/' and not n.endswith('/')]
    mismatch = sorted(set(names) ^ set(disk))
    content_bad = []
    for n in names:
        try:
            if z.read('migration-kit/' + n) != open(os.path.join(KIT, n), 'rb').read():
                content_bad.append(n)
        except FileNotFoundError:
            content_bad.append(n + ' (missing on disk)')
    zip_ok = (md5(zip_path) == 'bef571e6aac8e80a7dbc137ad6c0c0c1'
              and len(z.namelist()) == 97 and len(names) == 71
              and not mismatch and not content_bad)
    found.append(f"zip {zip_path} md5={md5(zip_path)} entries={len(z.namelist())}")
else:
    mismatch, content_bad = [], []
    skipped.append('shipped zip not found (looked next to checker, next to kit, '
                   '~/workspace, ~/Downloads; set $MIGRATION_KIT_ZIP)')
prompt_ok = True
if PROMPT:
    prompt_ok = md5(PROMPT) == '7b8937fd4eb7a9ff95fc1fe144b3b18e'
    found.append(f"prompt {PROMPT} md5={md5(PROMPT)}")
else:
    skipped.append('build prompt not found (looked next to checker, next to kit, '
                   '~/workspace, ~/Downloads; set $MIGRATION_KIT_PROMPT)')
disk_ok = len(disk) == 71 and not mismatch and not content_bad
check('M', 'M11', 'shipped zip + prompt md5s and contents',
      zip_ok and prompt_ok and disk_ok,
      f"found: {'; '.join(found) or 'none'} | skipped: {'; '.join(skipped) or 'none'} | "
      f"zip files={len(names)} disk files={len(disk)} "
      f"name mismatch={mismatch} content mismatch={content_bad}")

# ================= summary =================
fails = [r for r in RESULTS if not r[3]]
a_total = sum(1 for r in RESULTS if r[0] == 'A')
a_pass = sum(1 for r in RESULTS if r[0] == 'A' and r[3])
m_total = sum(1 for r in RESULTS if r[0] == 'M')
m_pass = sum(1 for r in RESULTS if r[0] == 'M' and r[3])
f_total = sum(1 for r in RESULTS if r[0] == 'F')
f_pass = sum(1 for r in RESULTS if r[0] == 'F' and r[3])
print(f"\n===== SUMMARY =====")
print(f"assertions: {a_pass}/{a_total} pass (plus guard G counted separately)")
print(f"mechanism checklist: {m_pass}/{m_total} pass")
print(f"flagged item: {f_pass}/{f_total} pass")
print(f"FAILURES: {len(fails)}")
for kind, cid, name, ok, ev in fails:
    print(f"  FAIL {kind} {cid}: {name} :: {ev}")
with open(f'{V}/results.json', 'w') as f:
    json.dump([{'kind': k, 'id': str(c), 'name': n, 'ok': o, 'evidence': e}
               for k, c, n, o, e in RESULTS], f, indent=1)
print(f"results written to {V}/results.json")

# Migration Analysis Kit

Machine-built inventory of the Java + Oracle estate: process dependency map, code
dependency map, database dependency map, and a DB-heavy / Java-heavy classification
per process. Built for the Risk_DB / Risk_DL / CoreWM migration to Azure Databricks.

## How it works

Three steps, in order:

1. **Deterministic scan** (no agent tokens spent on reading code). The Python
   scripts in `scripts/` walk the three repos and the Control-M export and
   extract: JDBC call sites, SQL string literals (including `+`-concatenated
   ones), stored-procedure calls, Oracle dialect features, Java imports, PL/SQL
   object definitions and references, and job→command→code links. `build_graph.py`
   merges this into a dependency graph plus one evidence bundle per process;
   `classify.py` adds a first-pass DB-heavy / Java-heavy label.
   `build_graph.py` also infers **likely data-flow dependencies**: when unit A
   writes a table that unit B reads (including writes/reads that happen
   *transitively* inside a called PL/SQL procedure — recorded as
   `write_via_proc` / `read_via_proc`), it adds a `likely_depends_on` edge
   (B → A, with the shared tables in `via`) plus per-unit
   `likely_depends_on` / `likely_required_by` lists. This is a heuristic
   stand-in for the scheduler DAG when Control-M data is unavailable — agents
   validate the edges during review, and humans confirm before wave planning.
   Run by preferred AI directly — ask in chat, or run the commands yourself (see below).
2. **Agent review** (the expensive part). The saved workflow
   **`oracle-migration-review`** fans out small agents over batches of processes.
   Each agent confirms the classification, writes a 2–3 line summary, and flags
   ambiguities — following `prompts/classify_process.md`. Every unit writes its
   own checkpoint file (`state/units/<unit_id>.json`), so a re-run skips finished
   work automatically.
3. **Reports.** The per-unit results are compiled into the final catalogs and
   reports (ask preferred AI, or follow the report prompt pattern yourself).

Phase 1 does the heavy lifting so Phase 2 stays small: agents review evidence
bundles instead of reading 15 years of code from scratch.

## Prerequisites

- The three repos checked out locally (read-only access is enough).
- Optionally, a job scheduler export: a Control-M XML export, an Autosys CSV
  (columns `JOB_ID,JOB_NAME,JOB_DESCRIPTION,COMMAND,CONDITION,PARENT_JOB_NAME,
  TASK_NAME,PARAMETERS`), or a simple CSV with columns `job_name, command,
  depends_on, schedule` (`depends_on` semicolon-separated).
- Python 3.8+ (stdlib only — no pip packages needed).

## Running it

**Step 1 — point it at your code.** Fill in the repo paths (and job export path,
if you have one) in `config.yaml`.

**Step 2 — scan.** Ask preferred AI to run the scan, or do it yourself:

```
cd ~/workspace/migration-kit
mkdir -p state/raw state/units state/graph output
for r in Risk_DB Risk_DL CoreWM; do
  python3 scripts/scan_java.py  --root <path-to-$r> --repo $r --out state/raw/java_$r.json
  python3 scripts/scan_plsql.py --root <path-to-$r> --repo $r \
      --exts ".sql,.pls,.pks,.pkb,.fnc,.prc,.trg,.vw,.tab,.seq" --out state/raw/plsql_$r.json
done
python3 scripts/scan_jobs.py --export <jobs.xml|jobs.csv> --out state/raw/jobs.json
# --format auto-detects controlm (XML/CSV) vs autosys (CSV with a JOB_ID column);
# ...or: echo '[]' > state/raw/jobs.json   (no scheduler data -> units are Java main() classes)
python3 -c "import json,glob; j=[]; [j.extend(json.load(open(f))) for f in glob.glob('state/raw/java_*.json')]; json.dump(j,open('state/raw/java_all.json','w')); p=[]; [p.extend(json.load(open(f))) for f in glob.glob('state/raw/plsql_*.json')]; json.dump(p,open('state/raw/plsql_all.json','w'))"
python3 scripts/build_graph.py --java state/raw/java_all.json --plsql state/raw/plsql_all.json \
    --jobs state/raw/jobs.json --outdir state/graph
python3 scripts/classify.py --units state/graph/units.json \
    --db-heavy-ratio 1.6 --java-heavy-ratio 0.6 --min-evidence 3
```

Without a scheduler export, the graph builder falls back to one unit per Java
`main()` class.

**Autosys CSV notes.** The expected columns are
`JOB_ID,JOB_NAME,JOB_DESCRIPTION,COMMAND,CONDITION,PARENT_JOB_NAME,TASK_NAME,PARAMETERS`.
Rows are grouped by `JOB_NAME` (one row per condition is the normal shape);
exact duplicate rows are dropped and it warns if one job has conflicting
`COMMAND` values. `CONDITION` cells are parsed into dependencies — plain names
as well as Autosys expressions like `s(JOB_A) AND s(JOB_B)`. `PARENT_JOB_NAME`
becomes a `child_of` edge (kept separate from condition-based `depends_on`
edges). If `COMMAND` yields no code linkage, `TASK_NAME` + `PARAMETERS` is tried
as a fallback.

**Step 3 — review.** Ask preffered AI to launch the `oracle-migration-review` workflow
with your unit batches (preferred AI builds the batch list from `state/graph/units.json`
and any checkpoint files already in `state/units/`).

**Step 4 — reports.** Ask preferred AI to compile the reports from `state/`.

## Resume / quota safety — read this

- **Checkpoints are the source of truth.** Every finished unit leaves
  `state/units/<unit_id>.json`. To resume after an interruption or quota
  exhaustion, **relaunch the review workflow fresh with the same args** — batch
  agents skip units that already have checkpoints and only do the missing ones.
  (Verified behavior; do NOT use the runtime's result-cache resume for this —
  it reuses old batch results without re-checking the checkpoint files.)
- **To burn quota slower:** run a trial slice first (a few batches), review only
  the `NEEDS_REVIEW` / `UNKNOWN` units, lower `concurrency`, or raise the batch
  size. Each batch agent carries substantial input-token overhead, so fewer,
  larger batches are cheaper than many tiny ones — up to ~12 units per batch is
  a good balance for review quality.
- Deleting `state/` forces a full re-run (scan is cheap; review re-does everything).

## Why the workflow is "blind" (a runtime note)

In this environment a workflow's `agent()` calls always return empty results to
the workflow script, so the review workflow is deliberately designed to need
nothing back: batches arrive via launch args, agents coordinate through the
checkpoint files, and the final message just points at the outputs. All real
data flows through files, which is also what makes resume reliable.

## Outputs

- `state/graph/dependency-graph.json` — nodes (jobs, Java classes, DB objects)
  and edges (invokes, imports, reads, writes, calls, plus `depends_on` from
  Control-M and heuristic `likely_depends_on` from data-flow inference).
- `state/graph/units.json` — per-process evidence bundles + pre-classification
  + `likely_depends_on` / `likely_required_by` lists. `db_objects` kinds:
  `read` / `write` (direct JDBC/SQL evidence), `read_via_proc` /
  `write_via_proc` (transitive through a called PL/SQL procedure), `call`.
- Final reports (in the agreed output dir):
  - `process-catalog.csv` — one row per process: classification, key code units,
    DB objects touched, signal counts, agent summary.
  - `classification-report.md` — DB-heavy vs Java-heavy breakdown, pattern
    notes, and the explicit list of ambiguous items needing human eyes.
  - `oracle-dialect-census.csv` — counts of each Oracle-specific SQL feature
    (`ROWNUM`, `NVL`, `CONNECT BY`, sequences, …). This sizes the SQL-translation work.
  - `db-object-catalog.csv` — every Oracle object found, where it's defined,
    and which processes touch it.
  - `dependency-graph.json` — the full graph.

## Layout

- `config.yaml` — repo paths, thresholds, workflow knobs.
- `scripts/` — deterministic scanners (stdlib-only Python).
- `prompts/` — the agent prompt template used in review.
- `fixtures/` — tiny synthetic repos used to self-test the scanners and workflow.
- `state/` — checkpoints + intermediate scan data (created at runtime).
- `output/` — reserved for final reports (created at runtime).
- `trial-output/` — leftover from self-testing (safe to delete).

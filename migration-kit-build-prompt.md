# Prompt: rebuild the Oracle→Azure migration analysis kit

Copy everything below the line into any capable model, chat, or coding CLI.
It is a complete, self-contained build spec. The model should create every
file, then self-test on the fixtures before declaring done.

---

You are a senior engineer building a **codebase analysis kit** for a large
legacy migration. Build every file below exactly as specified, using only
Python standard library (no pip dependencies). Then self-test as specified.

## 1. Context

- 15-year-old Java + Oracle risk platform, ~4,000 batch processes, 3 repos:
  - `Database`: all Oracle code (tables, views, packages/procedures/functions)
  - `DataLayer`: process code + shared/common code used by the other repo
  - `CoreCalculators`: majority of the Java code
- Processes are batch jobs (scheduler: Autosys; Control-M format also supported).
  They predominantly read from Oracle via JDBC, process in Java and/or PL/SQL,
  and write back to Oracle. Bulk writes, ACID transactions relied upon.
- Migration target: keep Java, replace Oracle with Azure Databricks / ADLS Gen2
  + Spark/SparkSQL. Strategy is a "thin swap" of the data layer.
- There is **no automated test suite**. Verification will be pre/post result
  comparison. Token quota is a concern: the kit must minimize LLM token spend
  and every long-running step must be **checkpointed and resumable**.

## 2. Non-negotiable design decisions

1. **Deterministic first.** Python scripts (regex/parse-based, zero LLM tokens
   spent reading code) do the heavy lifting: inventory, dependency extraction,
   first-pass classification. LLM agents are used ONLY for per-process review
   of pre-built evidence bundles — never for bulk code reading.
2. **Checkpoint everything.** Each reviewed process writes its own result file.
   Re-running skips finished units automatically. Resume = fresh re-run that
   skips existing checkpoint files.
3. **Honest uncertainty.** Anything the evidence cannot support must be labeled
   `NEEDS_REVIEW` / `UNKNOWN` and listed explicitly — never silently dropped,
   never invented.
4. **Stdlib-only Python 3.8+.** No third-party packages anywhere.

## 3. Kit layout to create

```
migration-kit/
  config.yaml
  README.md
  scripts/
    scan_java.py
    scan_plsql.py
    scan_jobs.py
    build_graph.py
    classify.py
    make_batches.py
  prompts/
    classify_process.md
  fixtures/
    Risk_DB/ddl/risk_tables.sql
    Risk_DB/pkg/risk_calc_pkg.pkb
    Risk_DL/common/com/risk/dl/DbUtil.java
    CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java
    CoreWM/jobs/com/risk/wm/jobs/RiskReportJob.java
    controlm/jobs.xml
    autosys/jobs.csv
```

## 4. Script specifications

### 4.1 `scripts/scan_java.py`
`scan_java.py --root <dir> --repo <name> --out <file.json>`

Walk `<dir>` for `*.java`. For each file (read with utf-8/errors=replace):

- Strip block and line comments (but extract string literals FIRST).
- Extract string literals: `"..."` (with escapes) and `"""..."""` text blocks.
  **Merge literals concatenated with `+`** (the standard Java SQL-building
  pattern), allowing whitespace/newlines and at most one simple variable
  expression between them — replace the variable with ` ? `. This matters:
  without merging, SQL fragments split across literals are missed.
- Record `package`, `imports` (list), `classes` (list).
- Count JDBC signals (regex on comment-stripped code): `getConnection`,
  `prepareStatement`, `prepareCall`, `createStatement`, `executeQuery`,
  `executeUpdate`, `executeBatch`, `addBatch`, `setAutoCommit`, `commit`,
  `rollback`, `setSavepoint`.
- A merged literal is a "SQL literal" if it matches
  `\b(SELECT|INSERT|UPDATE|DELETE|MERGE|WITH|TRUNCATE|CALL|CREATE|DROP|ALTER)\b`
  (case-insensitive).
- From SQL literals extract, with read/write kinds:
  - read: `\bFROM\s+(name)`, `\bJOIN\s+(name)`, `\bTABLE\s*\(\s*(name)`
  - write: `\bINTO\s+(name)` **only when preceded by INSERT** (PL/SQL
    `SELECT ... INTO var` must NOT count), `\bUPDATE\s+(name)\s+SET`,
    `\bMERGE\s+INTO\s+(name)`. Skip the pseudo-table `DUAL`.
  - Table names: `[A-Za-z_][\w$]*` optionally schema-qualified; uppercase them.
- Stored-procedure calls: `\{\s*call\s+([A-Za-z_][\w$.]*)` inside SQL literals,
  plus `prepareCall("...")` in code (strip whitespace/braces, uppercase).
- Oracle dialect features — count occurrences **within the SQL literals**:
  `ROWNUM`, `SYSDATE|SYSTIMESTAMP|CURRENT_DATE`, `FROM DUAL`, `NVL(`/`NVL2(`,
  `DECODE(`, `(+)`, `CONNECT BY`/`START WITH`, `NEXTVAL`/`CURRVAL`,
  `FOR UPDATE`, `/*+` hints, `MODEL`, `PIVOT`.
- Metrics: `loc` (non-blank lines of comment-stripped code), `branches`
  (count of `if|for|while|switch|catch|case`), `methods_rough` (regex for method
  declarations), `has_main` (boolean: `public static void main(` present).
- Output JSON array, one record per file, with fields:
  `path, repo, package, classes, imports, jdbc{signal:count, only nonzero},
  sql_literal_count, sql_sample (first 10 literals, whitespace-collapsed,
  truncated to 300 chars), tables_read[], tables_written[], procs_called[],
  oracle_isms{name:count, only nonzero}, metrics{loc, branches, methods_rough, has_main}`.

### 4.2 `scripts/scan_plsql.py`
`scan_plsql.py --root <dir> --repo <name> --exts ".sql,.pks,.pkb,..." --out <file.json>`

Walk `<dir>` for the given extensions (case-insensitive). For each file:

- Strip `--` line comments and `/* */` block comments; also build a
  no-string-literals copy (replace `'...'` handling `''` escapes) for reference
  extraction.
- Definitions: regex for
  `CREATE [OR REPLACE] [FORCE|EDITIONABLE|NONEDITIONABLE] (TABLE|VIEW|
  MATERIALIZED VIEW|PACKAGE BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|SEQUENCE|
  SYNONYM|TYPE|INDEX) [schema.]name` → `{kind, name}` (name uppercased,
  schema-qualified when present).
- References with kinds: FROM/JOIN/TABLE( → `read`; INSERT INTO / UPDATE..SET /
  MERGE INTO → `write`. Uppercase names.
- Calls: `\b(\w+)\.(\w+)\s*\(` → `PKG.PROC` (uppercase); also
  `^\s*(EXEC|EXECUTE)\s+(name)`. **Exclude** `EXECUTE IMMEDIATE` from calls
  (track it under risky constructs instead).
- Risky constructs (count): `EXECUTE IMMEDIATE`, `DBMS_*`, `UTL_*`,
  `AUTONOMOUS_TRANSACTION`, `BULK COLLECT`, `FORALL`, `GOTO`.
- Output per file: `path, repo, definitions[], references[] (union, sorted),
  reads[], writes[], calls[] (sorted), risky_constructs{name:count},
  metrics{loc, def_count}`.

### 4.3 `scripts/scan_jobs.py`
`scan_jobs.py --export <file> --out <file.json> [--format auto|controlm|autosys]`

Parse scheduler exports into job records. Auto-detect: `.xml` → controlm;
otherwise sniff the CSV header — a `JOB_ID` column means autosys, else controlm.

**Control-M XML:** tolerant parse — find elements whose tag ends in `JOB`
(case-insensitive); read attributes `JOBNAME|NAME`, `CMDLINE|COMMAND|CMD`,
`NODEID|HOST`, `DESCRIPTION`; child `INCOND` elements' `NAME` attributes are
dependencies.

**Control-M CSV:** columns `job_name, command, depends_on, schedule`
(`depends_on` semicolon-separated).

**Autosys CSV:** columns
`JOB_ID,JOB_NAME,JOB_DESCRIPTION,COMMAND,CONDITION,PARENT_JOB_NAME,TASK_NAME,PARAMETERS`.
- The sheet has **one row per condition** and may contain duplicate rows:
  drop exact-duplicate rows (report the count), group rows by `JOB_NAME`
  (fall back to `JOB_ID` when the name is blank), aggregate distinct
  `CONDITION` values into `depends_on`, collect distinct `PARENT_JOB_NAME`
  into `parent_jobs`, take the first non-blank value for scalar fields, and
  **warn** when one job has conflicting `COMMAND` values.
- Parse each `CONDITION` cell into job names: extract `s(name)` /
  `success(name)` groups first; if none, split on `AND`/`OR`/`;` and strip
  quotes/parens. Dedupe while preserving order.
- `dissect_command(cmd)`: extract a Java main class —
  first try `java <jvm-flags> <com.foo.Bar>`; else the first fully-qualified
  name with 2+ dots ending in an uppercase segment; plus `*.sh|ksh|bash|bat|
  cmd|ps1` script paths and `*.jar` paths. If `COMMAND` yields no linkage,
  retry dissection on `TASK_NAME + PARAMETERS`.
- Output per job: `job_name, job_id, description, command, host, schedule,
  depends_on[], parent_jobs[], task_name, parameters, main_class, scripts[],
  jars[]`. Print a summary line: rows read, duplicates dropped, jobs parsed,
  how many have code linkage.

### 4.4 `scripts/build_graph.py`
`build_graph.py --java <java_all.json> --plsql <plsql_all.json> --jobs <jobs.json> --outdir <dir>`

Inputs are merged scanner outputs (merge the per-repo files with a small
Python snippet). Build:

- **Class index:** fully-qualified name (`package.Class`) → file record;
  simple-name → FQNs.
- **DB object index:** from PL/SQL definitions; also create nodes for
  referenced-but-undefined objects. Per-object read/write sets from the
  PL/SQL `reads`/`writes` (attribute a file's sets to each object it defines).
- **Edges** (each `{src, dst, kind}`, plus `via` for inferred ones):
  - `job:<name>` → `java:<FQN>` (`invokes`; `invokes_maybe` when matched only
    by simple class name), → `script:<path>` (`invokes`)
  - `job:<a>` → `job:<b>` (`depends_on`), (`child_of`) for parent jobs
  - `java:<fqn>` → `java:<fqn>` (`imports`, exact-FQN imports only)
  - `java:<fqn>` → `db:<T>` (`reads` / `writes` / `calls`)
  - `db:<owner>` → `db:<ref>` (`references` / `calls`); skip self-edges
- **Units (one per process):**
  - If jobs exist: one unit per job. Entry points = linked main class / script.
  - If no jobs: one unit per Java class with `has_main`, unit_id
    `main:<FQN>`.
  - Reachable files = entry files + import closure, depth-limited to 3.
  - Aggregate metrics (loc, branches, methods, sql_literals, jdbc_sites,
    proc_calls, oracle_ism_total, write-signal count).
  - `db_objects`: `[{name, kind}]` where kind ∈ `read|write` (direct),
    `read_via_proc|write_via_proc` (**transitive**: the unit calls a PL/SQL
    procedure/package that reads/writes the table — resolve `PKG.PROC` then
    fall back to `PKG`), `call`. Direct `write` beats transitive kinds.
  - Fields: `unit_id, job_name, command, schedule, depends_on[],
    parent_jobs[], entry_points[], java_file_count, java_files[{path, repo,
    classes}], metrics{}, db_objects[], oracle_isms{}, repos[], linkage
    ('ok'|'missing')`.
- **Data-flow inference** (heuristic, for wave ordering when scheduler data is
  thin): if unit A writes a table (direct or via proc) that unit B reads, add
  edge `job:<B>` → `job:<A>` kind `likely_depends_on` with `via: [tables]`;
  exclude self-pairs. Also set per-unit `likely_depends_on[]` and
  `likely_required_by[]`.
- Write `<outdir>/dependency-graph.json` (`{nodes[], edges[]}`),
  `<outdir>/units.json`, `<outdir>/db-object-catalog.json`
  (`[{object, kind, defined_in[]}]`). Print node/edge/unit/inferred counts.

### 4.5 `scripts/classify.py`
`classify.py --units <units.json> --db-heavy-ratio 1.6 --java-heavy-ratio 0.6 --min-evidence 3`

Deterministic first-pass label per unit. With `m = unit.metrics`:

```
db_score   = 1.5*sql_literals + 4*proc_calls + 1*len(db_objects)
             + 0.5*oracle_ism_total + 1*writes
java_score = loc/40 + branches/8 + methods/3 + 0.5*jdbc_sites
evidence   = sql_literals + proc_calls + len(db_objects) + jdbc_sites + loc//100
ratio      = db_score / max(java_score, 0.5)
```

- `linkage == 'missing'` or `evidence < min_evidence` → `NEEDS_REVIEW`
- `ratio >= db_heavy_ratio` → `DB_HEAVY`
- `ratio <= java_heavy_ratio` → `JAVA_HEAVY`
- else → `BALANCED`

Store under `unit.preclassification = {label, db_score, java_score, ratio,
reasons[]}` (reasons = human-readable score breakdown). Print label counts.

### 4.6 `scripts/make_batches.py`
`make_batches.py --units <units.json> --checkpoints <state/units> --batch-size 12 [--only-labels NEEDS_REVIEW] [--limit N] --out <batches.json>`

Compute pending units: `sanitize(unit_id)` not among existing checkpoint
basenames, where `sanitize` replaces every character not in `[A-Za-z0-9_.-]`
with `_`. Optional label filter and limit. Emit JSON array of arrays (batches
of unit_ids). Print totals.

### 4.7 `prompts/classify_process.md`

The LLM review prompt. It must instruct the reviewer to: read the template's
task for one process — load the unit's evidence bundle from `units.json`,
read source files only if evidence is thin (entry point first, stay focused);
decide `DB_HEAVY | JAVA_HEAVY | BALANCED | UNKNOWN` with the given definitions
(DB-heavy = behavior dominated by SQL/stored procedures/set transforms;
Java-heavy = dominated by Java logic with the DB as mostly dumb storage);
write a 2–3 sentence summary; list the 3–5 most migration-relevant DB objects
with one-line reasons; sanity-check the `likely_depends_on` /
`likely_required_by` edges (confirm/correct/supplement); flag human-review
items (transaction/rollback reliance, Oracle-only features with no Spark
equivalent, dynamic SQL, ambiguous linkage, evidence gaps); and return ONLY
this JSON (no fences, no commentary):

```json
{
  "unit_id": "<same id>",
  "verdict": "DB_HEAVY | JAVA_HEAVY | BALANCED | UNKNOWN",
  "confidence": "high | medium | low",
  "agree_with_preclassification": true,
  "summary": "<2-3 sentences>",
  "key_db_objects": [{"object": "<name>", "why": "<one line>"}],
  "migration_notes": ["<short note>"],
  "human_review_flags": ["<short flag>"],
  "evidence_gaps": ["<what was missing, if anything>"]
}
```

Stress: a low-confidence verdict with clear gaps beats a confident guess; never
invent objects or behaviors not supported by evidence.

### 4.8 `config.yaml`

```yaml
repos:
  risk_db: ""    # path to Risk_DB checkout
  risk_dl: ""    # path to Risk_DL checkout
  corewm:  ""    # path to CoreWM checkout
job_export: ""   # scheduler export: Control-M XML/CSV or Autosys CSV ("" = skip)
plsql_extensions: [".sql", ".pls", ".pks", ".pkb", ".fnc", ".prc", ".trg", ".vw", ".tab", ".seq"]
output_dir: "output"
state_dir: "state"
analysis:
  db_heavy_ratio: 1.6
  java_heavy_ratio: 0.6
  min_evidence: 3
workflow:
  concurrency: 4
  batch_size: 12
```

### 4.9 `README.md`

Document: purpose; the 3-step run (1. fill config, 2. run scan scripts in the
given order, 3. LLM review of batches, 4. compile reports); the Autosys CSV
contract and dedup/grouping behavior; the data-flow inference and its
heuristic caveats; the `read|write|read_via_proc|write_via_proc|call` kinds;
and the resume contract below.

## 5. Review orchestration (LLM loop) specification

The long-running review must work like this (implement with whatever
orchestration the host supports — background agents, a script driving an LLM
CLI, or manual batches):

1. Build the batch list with `make_batches.py` (default 8–12 unit IDs per
   batch; each batch agent carries significant input-token overhead, so fewer
   larger batches are cheaper than many tiny ones).
2. Each batch worker, per unit ID in order:
   - Compute the checkpoint filename via the sanitize rule; **skip the unit
     entirely if the file already exists**.
   - Otherwise follow `prompts/classify_process.md` and write the verdict JSON
     to `state/units/<sanitized>.json` **immediately** (one file per unit, as
     it goes — never batch writes at the end, so an interruption loses minimal
     work).
3. **Resume = re-run that skips existing checkpoints.** Never re-do finished
   units. Deleting `state/` forces a full re-run.
4. After review, compile from `state/`:
   - `process-catalog.csv` — `unit_id, preclassification, verdict,
     confidence, summary (≤300 chars, one line), key_db_objects
     (semicolon-joined), java_file_count, repos (semicolon-joined), linkage`
   - `classification-report.md` — counts by label and verdict; what each
     class means for migration planning; explicit UNKNOWN/NEEDS_REVIEW lists;
     units with no verdict file; human-review flags aggregated with counts;
     a note on shared-library (Risk_DL) usage
   - `oracle-dialect-census.csv` — `feature, occurrences`, aggregated over
     `oracle_isms` (Java) + `risky_constructs` (PL/SQL), sorted descending —
     this sizes the SQL-translation work
   - `db-object-catalog.csv` — `object, kind, defined_in
     (semicolon-joined), used_by_units (semicolon-joined)`; flag objects that
     are referenced but have no definition in the scanned repos

## 6. Fixtures + self-test (must pass before done)

Create small synthetic fixtures exercising every feature:

- `Risk_DB/ddl/risk_tables.sql`: `CREATE TABLE RISK_POSITIONS (...)`,
  `CREATE OR REPLACE VIEW V_RISK_SUMMARY AS SELECT ... FROM RISK_POSITIONS`
- `Risk_DB/pkg/risk_calc_pkg.pkb`: `CREATE OR REPLACE PACKAGE BODY
  RISK_CALC_PKG` with a procedure that `SELECT SUM...INTO` from
  RISK_POSITIONS, `INSERT INTO RISK_RESULTS ... SELECT ... FROM
  RISK_POSITIONS`, an `EXECUTE IMMEDIATE`, and a `DBMS_OUTPUT.PUT_LINE`
- `Risk_DL/common/com/risk/dl/DbUtil.java`: shared class with a
  `prepareStatement` + `addBatch`/`executeBatch` bulk insert into
  RISK_POSITIONS and a `commit()`
- `CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java`: `main()` class importing
  the shared `DbUtil`; JDBC with `setAutoCommit(false)`/`commit`/`rollback`;
  a `+`-concatenated SQL string using `ROWNUM`; a `{call
  RISK_CALC_PKG.CALC_EXPOSURE(?)}` via `prepareCall`; an `NVL`/`SYSDATE` query
- `CoreWM/jobs/com/risk/wm/jobs/RiskReportJob.java`: `main()` class reading
  `RISK_RESULTS` with plain JDBC
- `controlm/jobs.xml`: two `<JOB>` elements, one invoking the nightly class
  via `java ... com.risk.wm.jobs.NightlyRiskJob`, one running a `.sh` script,
  with `INCOND` dependencies
- `autosys/jobs.csv`: the 9-column Autosys layout, including an exact duplicate
  row, two rows for one job with different `CONDITION`s (one a
  `s(A) AND s(B)` expression), and a `PARENT_JOB_NAME`

Self-test assertions (run the scripts, verify):

1. `scan_java.py` on the fixtures finds `ROWNUM`, `SYSDATE`, `NVL` (proves
   literal-chain merging works), the `{call ...}` proc, `tables_read =
   [RISK_POSITIONS, RISK_RESULTS]`, the `DbUtil` import, `has_main = true`.
2. `scan_plsql.py` finds the TABLE, VIEW, and PACKAGE BODY definitions;
   `RISK_RESULTS` appears under `writes` (not just references);
   `EXECUTE IMMEDIATE` is under risky constructs, NOT under calls.
3. `scan_jobs.py --format autosys` reports the duplicate row dropped and
   aggregates both conditions; `--format` auto-detects from the `JOB_ID`
   column; the Control-M XML still parses.
4. `build_graph.py` with `jobs: []` creates units from the two `main()`
   classes and produces a `likely_depends_on` edge from the report job to the
   nightly job via `RISK_RESULTS` (transitively through the package =
   `write_via_proc` on the nightly job).
5. `classify.py` labels the nightly job `BALANCED` (or `DB_HEAVY`) and the
   script-only job `NEEDS_REVIEW`.
6. `make_batches.py` excludes checkpointed units and honors `--only-labels`.

## 7. Definition of done

- Every file exists at the specified path; all scripts are stdlib-only and
  executable via the documented CLI flags.
- All six self-test assertions pass; paste the verification output.
- The review prompt, config, and README are complete and consistent with the
  scripts (flag names, JSON field names, and the checkpoint sanitize rule
  match exactly).
- No LLM is required to run the scan phase; the review phase never re-does a
  checkpointed unit.

Build it now.

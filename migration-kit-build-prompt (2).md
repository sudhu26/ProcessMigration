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
  - `Risk_DB`: all Oracle code (tables, views, packages/procedures/functions)
  - `Risk_DL`: process code + shared/common code used by the other repo
  - `CoreWM`: majority of the Java code
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
    scan_scripts.py
    build_graph.py
    classify.py
    make_batches.py
    build_process_docs.py
    build_table_catalog.py
    translate_sql.py
    prepare_migration_tree.py
    ignore_lists.py
    run_process.py
    learn_patterns.py
  prompts/
    classify_process.md
  fixtures/
    ignore.json
    exemplars/
    Risk_DB/ddl/risk_tables.sql
    Risk_DB/pkg/risk_calc_pkg.pkb
    Risk_DB/sql/post_load.sql
    Risk_DL/common/com/risk/dl/DbUtil.java
    CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java
    CoreWM/jobs/com/risk/wm/jobs/RiskReportJob.java
    CoreWM/jobs/LegacyJob.java
    scripts/run_nightly.sh
    controlm/jobs.xml
    autosys/jobs.csv
```

## 4. Script specifications

### 4.1 `scripts/scan_java.py`
`scan_java.py --root <dir> --repo <name> --out <file.json> [--ignore ignore.json]`

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
- Column extraction (best-effort, per SQL literal): `INSERT INTO t (c1, c2)`
  → write columns; `UPDATE t SET c1=, c2=` → write columns; the `SELECT` list
  → read columns, attributed via `prefix.col` against the FROM/JOIN tables
  and aliases, or to the single FROM table when unambiguous (multi-table bare
  columns go to `columns_unattributed`). Strip `AS alias`, implicit aliases
  (`SUM(x) E`, `BOOK b`), and `SELECT ... INTO var`. `*` is recorded as `*`.
  Recurse into derived-table subqueries (`SELECT * FROM (SELECT ...)`).
  Output fields: `table_columns{t:{read:[], write:[]}}`,
  `columns_unattributed[]`.
- Ignore lists (see 4.14): with `--ignore`, imports matching `java_imports`
  patterns and tables/procs matching `oracle_objects` patterns are excluded
  from `imports`/`tables_read`/`tables_written`/`procs_called`/`table_columns`
  and recorded instead in `ignored_imports[]`, `ignored_tables[]`,
  `ignored_procs[]`, `ignored_table_columns{}`.

### 4.2 `scripts/scan_plsql.py`
`scan_plsql.py --root <dir> --repo <name> --exts ".sql,.pks,.pkb,..." --out <file.json> [--ignore ignore.json]`

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
- Columns: `ddl_columns{t:[cols]}` from `CREATE TABLE` column lists (skip
  CONSTRAINT/PRIMARY/FOREIGN/UNIQUE/CHECK items); `table_columns` and
  `columns_unattributed` with the same rules as scan_java.py, applied per
  `;`-separated statement.
- Ignore lists (see 4.14): with `--ignore`, reads/writes/calls matching
  `oracle_objects` patterns are excluded from `reads`/`writes`/`references`/
  `calls`/`ddl_columns`/`table_columns` and recorded in `ignored_tables[]` /
  `ignored_calls[]`; the `dbms`/`utl` risky-construct counts only count
  non-ignored matches. Definitions are never ignored.
- Output per file: `path, repo, definitions[], references[] (union, sorted),
  reads[], writes[], ignored_tables[], ignored_calls[], ddl_columns{}, table_columns{}, columns_unattributed[],
  calls[] (sorted), risky_constructs{name:count},
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

### 4.4 `scripts/scan_scripts.py`
`scan_scripts.py --root <dir> --repo <name> --out <file.json>`

Walk `<dir>` for `*.sh, *.ksh, *.bash, *.pl, *.py`. For each script (full-line
`#` comments stripped):
- Java main classes via the same `java <flags> <com.foo.Bar>` regex as
  `scan_jobs.py`; `*.jar` paths; other invoked scripts (`*.sh|ksh|bash|pl|py`,
  including `source`/`.` sourcing, excluding the script's own name);
  `sqlplus ... @file.sql` targets; `python script.py` targets.
- Output per script: `path, repo, java_mains[], jars[], scripts_called[],
  sql_files[], python_files[]`.

### 4.5 `scripts/build_graph.py`
`build_graph.py --java <java_all.json> --plsql <plsql_all.json>
--jobs <jobs.json> [--scripts <scripts_all.json>] [--ignore ignore.json]
[--only UNIT1,UNIT2] --outdir <dir>`

Inputs are merged scanner outputs (merge the per-repo files with a small
Python snippet). With `--ignore`, an `apply_ignore` step filters the loaded
records first (imports, tables, procs, calls, references, ddl/table columns)
so data scanned before the list existed is filtered without re-scanning. With
`--only`, dependency inference still runs over the full estate, then the
outputs are pruned to the named units: units.json keeps only them, the graph
keeps their 3-hop neighbourhood (scripts, jars, Java files, DB objects,
upstream/downstream jobs), and the catalog keeps only the objects they touch
(a unit touching `PKG.PROC` keeps the `PKG` catalog entry). Build:

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
  - `script:<path>` → `sql:<path>` (`invokes`) for SQL files a script calls
    via `sqlplus @file`, resolved by basename
- **Units (one per process):**
  - One unit per scheduler job. Entry points = linked main class / script,
    expanded through scripts: a job invoking a wrapper script gets the Java
    classes the script invokes (script→script chains followed, depth-safe via
    visited set) and the SQL files it calls via `sqlplus @file` (resolved by
    basename, since production paths in job commands rarely match checkout
    paths). SQL reached this way contributes `read_via_script` /
    `write_via_script` table references and its procedure calls. The same
    expansion applies in reverse: a job whose command calls Java directly
    also picks up wrapper scripts that invoke the same entry main, so the
    wrapper's `sqlplus` files belong to the process too.
  - **Scheduler data is treated as possibly incomplete:** every Java
    `main()` class not referenced by any job (exact or class-name match)
    automatically becomes a `synthetic: true` unit (`unit_id =
    main:<FQN>`), so code-discovered processes are never silently dropped.
  - Reachable files = entry files + import closure, depth-limited to 3.
  - Aggregate metrics (loc, branches, methods, sql_literals, jdbc_sites,
    proc_calls, oracle_ism_total, write-signal count).
  - `db_objects`: `[{name, kind, columns_read[], columns_written[]}]` where
    kind ∈ `read|write` (direct),
    `read_via_proc|write_via_proc` (transitive one hop: the unit calls a
    PL/SQL procedure/package that reads/writes the table — resolve
    `PKG.PROC` then fall back to `PKG`), `read_via_script|write_via_script`
    (from `sqlplus @file` SQL), `call`. Direct `write` beats transitive
    kinds. Columns are merged from the Java/PLSQL `table_columns` of the
    unit's files (direct + via-script + via-proc); `columns_unattributed[]`
    is kept on the unit. The db-object catalog gains a `columns[]` field
    (union of `ddl_columns` for that object).
  - Linkage quality per unit: `linkage = 'ok'` (exact FQN or script-resolved
    match), `'maybe'` (class-name-only match — typical when a file has no
    `package` declaration; all candidates listed in `linkage_candidates` for
    the reviewer to confirm), `'missing'` (no code linked at all).
  - Fields: `unit_id, job_name, command, schedule, depends_on[],
    parent_jobs[], entry_points[], java_file_count, java_files[{path, repo,
    classes}], metrics{}, db_objects[], oracle_isms{}, repos[], linkage,
    linkage_candidates[], synthetic, jars[]` (`jars` = basenames from the job
    command plus all reachable scripts).
- Script→code edges: `script:` → `java:` (`invokes`), `script:` → `script:`
  (`invokes`), `script:` → `jar:` (`uses`); job commands' jars also get
  `job:` → `jar:` (`uses`) edges. When one basename matches multiple scanned
  scripts, the builder warns and uses the first.
- **Data-flow inference** (heuristic, for wave ordering when scheduler data is
  thin): if unit A writes a table (kinds `write`, `write_via_proc`,
  `write_via_script`) that unit B reads (kinds `read`, `read_via_proc`,
  `read_via_script`), add edge `job:<B>` → `job:<A>` kind `likely_depends_on`
  with `via: [tables]`; exclude self-pairs. Also set per-unit
  `likely_depends_on[]` and `likely_required_by[]`.
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
  (`linkage == 'maybe'` still scores normally — the reviewer confirms the
  class-name match using `linkage_candidates`)
- `ratio >= db_heavy_ratio` → `DB_HEAVY`
- `ratio <= java_heavy_ratio` → `JAVA_HEAVY`
- else → `BALANCED`

Store under `unit.preclassification = {label, db_score, java_score, ratio,
reasons[]}` (reasons = human-readable score breakdown). Print label counts.

### 4.6 `scripts/make_batches.py`
`make_batches.py --units <units.json> --checkpoints <state/units> --batch-size 12 [--only-labels NEEDS_REVIEW] [--only UNIT1,UNIT2] [--limit N] --out <batches.json>`

Compute pending units: `sanitize(unit_id)` not among existing checkpoint
basenames, where `sanitize` replaces every character not in `[A-Za-z0-9_.-]`
with `_`. Optional label filter, optional unit-ID filter, and limit. Emit JSON array of arrays (batches
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
ignore_file: ""  # ignore.json path ("" = no ignores); see 4.14
patterns_file: ""  # patterns.json path ("" = no learned patterns); see 4.16
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
given order, 3. LLM review of batches, 4. compile reports); running one
process with `run_process.py --process <name>` (scan stays full, outputs
filtered); the Autosys CSV
contract and dedup/grouping behavior; the data-flow inference and its
heuristic caveats; the `read|write|read_via_proc|write_via_proc|call` kinds;
and the resume contract below.

### 4.10 `scripts/build_process_docs.py`
`build_process_docs.py --units units.json [--graph dependency-graph.json]
[--java java_all.json] [--verdicts state/units] [--only UNIT1,UNIT2]
--outdir output/process-docs`

One self-contained HTML document per process (`<sanitized unit_id>.html`, plus
`index.html`), inline CSS and inline SVG — no external assets. Each document
shows: badges (pre-classification, linkage, synthetic, review verdict);
"What this process does" — the review summary from the verdict checkpoint
when present, otherwise an auto-drafted 2–3 sentence description built from
evidence (entry points, tables read/written, procs called, size) clearly
marked DRAFT; key facts (command, schedule, entry points, repos, jars,
Oracle dialect features, linkage candidates); the dependency chain
(job → script → jar → Java → Oracle); Inputs and Outputs tables with columns
(a table counts as input if its kind is a read kind OR it has
`columns_read`, and as output if a write kind OR `columns_written` — the kind
field is the single dominant direction, columns tell the rest); stored
procedures called; upstream dependencies (scheduler `depends_on` vs inferred
`likely_depends_on` with the `via` tables, plus parent jobs); downstream
dependents; Java-to-Java dependencies (imports of entry files, resolved to
owning process via the `--java` records); Oracle-to-Oracle dependencies
(what each called procedure itself references/calls, looking up both the full
`PKG.PROC` node and the `PKG` node); one inline SVG dependency diagram
(layered: upstream jobs | this job | scripts/jars | Java | Oracle |
downstream jobs, capped at 5/6/10 nodes per layer with "+N more"); the review
section (verdict, migration notes, human review flags, evidence gaps); source
files. Escape all dynamic text with `html.escape`. The scripts shown for a
unit are the job's own scripts plus wrapper scripts that invoke the unit's
entry Java files (a job command often calls Java directly while a wrapper
script invokes the same main).

### 4.11 `scripts/build_table_catalog.py`
`build_table_catalog.py --units units.json --plsql plsql_all.json
[--only UNIT1,UNIT2] --out tables.md`

One Markdown file, one section per table: columns from `CREATE TABLE` DDL
(authoritative) or, when no DDL exists, columns observed in use; "Read by
(inputs)" — processes reading it, with columns read; "Written by (outputs)" —
processes writing it, with columns written. A table counts as read/written by
a process when its kind is a read/write kind OR it has read/write columns.
A summary table up front lists each table with DDL column count, reader/writer
counts, and whether it is defined in the repos (flag tables referenced but
never defined).

### 4.12 `scripts/translate_sql.py`
Deterministic first-pass Oracle → Spark SQL, stdlib only.
`translate(sql, ignored=None, overrides=None)` returns `{original, translated, applied[],
flags[], confidence}`. `ignored` is an `ignore_lists.IgnoreList`: calls to
ignored Oracle packages are not flagged (they are out of migration scope),
while other flags (ROWNUM, CONNECT BY, …) still apply. `overrides` is a list
of approved learned SQL overrides from `patterns.json`: a
whitespace-normalized exact statement match wins over the mechanical rules
and returns `applied: ["pattern:<id>"]`, `confidence: high`, no flags. Mechanical local
patterns only, applied outside string literals
and comments for keyword rules: `NVL(x,y)`→`COALESCE(x,y)`,
`NVL2(x,y,z)`→`CASE WHEN x IS NOT NULL THEN y ELSE z END`,
`SYSDATE`/`SYSTIMESTAMP`→`current_timestamp()`, `FROM DUAL` removed,
`/*+ */` hints removed, `TO_DATE`→`to_date` and `TO_CHAR`→`date_format`
with Oracle→Spark format-mask mapping (`YYYY`→`yyyy`, `MM`→`MM`, `DD`→`dd`,
`HH24`→`HH`, `MI`→`mm`, `SS`→`ss`, `MON`→`MMM`, `MONTH`→`MMMM`, `DY`→`EEE`,
`DAY`→`EEEE`; unmapped mask tokens flag the call and leave it unchanged),
`TRUNC(x)`/`TRUNC(x,'DD')`→`to_date(x)`, `TRUNC(x,'MM')`→
`date_trunc('month', x)`, `'YYYY'`→`date_trunc('year', x)`,
`'HH24'`→`date_trunc('hour', x)`, `DECODE(a,v1,r1,...,default)`→
`CASE a WHEN v1 THEN r1 ... [ELSE default] END` (paren-aware arg splitting).
Flagged, never guessed: `ROWNUM` (needs `ROW_NUMBER() OVER (ORDER BY ...)`),
`CONNECT BY`/`START WITH` (recursive CTE), `(+)` joins, `NEXTVAL`/`CURRVAL`,
`FOR UPDATE`, `EXECUTE IMMEDIATE`, `MODEL`, `PIVOT`, `DBMS_`/`UTL_` calls,
`MERGE` (review), `BULK COLLECT`/`FORALL`. Confidence: `needs-review` if any
flags, `medium` if DECODE/format mapping applied, else `high`.

### 4.13 `scripts/prepare_migration_tree.py`
`prepare_migration_tree.py --repos Risk_DB=<path>,Risk_DL=<path>,CoreWM=<path>
--outdir migration-tree [--ignore ignore.json]
[--only UNIT1,UNIT2 --units units.json --graph dependency-graph.json
--catalog db-object-catalog.json]`

Build the migration working tree: mirror each repo's folder/file structure
**exactly** (same relative paths) under `<outdir>/<repo>/`. Java files are
copied as-is except SQL string literals: detect `+`-concatenated literal
chains (same `between` rule as scan_java.py — a chain is SQL when its merged
content matches the SQL-keyword pattern), translate each fragment with
`translate_sql.py`, apply high/medium translations in place (re-escaping
string literals), and leave needs-review fragments unchanged with a
`/* TODO-SPARK: <flags> */` marker after the literal. PL/SQL files get the
first-pass translation applied to the whole file plus a header comment listing
applied rules and flag count. All other files are copied byte-for-byte. One
translation bundle per touched file at
`translation-bundles/<repo>/<relpath>.translate.json` (every literal/statement
with original, translated, applied rules, flags, confidence, and the merged
`chain_sql` for concatenated fragments). Write `translation-report.md`
(file counts, literals translated/flagged, list of files needing agent review).
Document the limits: patterns split across concatenated fragments are left
for the agent; `CREATE TABLE` DDL is copied as-is, not translated. With
`--ignore`, calls to ignored packages are not flagged for conversion. With
`--patterns patterns.json`, approved library mappings rewrite imports in
place, approved SQL overrides replace the mechanical rules (see 4.17), and
call sites using the old libraries are flagged in the bundle with the
pattern ID (never auto-rewritten); the report gains a patterns section. With
`--only`, mirror only the selected units' files: their Java files, the PL/SQL
files defining the objects they touch (catalog `defined_in`), and the scripts
and SQL files reachable from their jobs in the graph (3 undirected hops —
a wrapper script often reaches the job only via the Java main it invokes).

### 4.15 `scripts/run_process.py`
`run_process.py --process UNIT1[,UNIT2] --repos Risk_DB=<path>,... --jobs
<export> [--ignore ignore.json] [--patterns patterns.json]
[--outdir runs/<first-process>]`

One-command pipeline for a single process (or a few): runs the full scan over
the repos (the scan always covers the whole estate — dependency inference
needs it), then `build_graph.py --only`, `classify.py`,
`build_process_docs.py --only`, `build_table_catalog.py --only`,
`prepare_migration_tree.py --only`, and `make_batches.py --only`, all under
`<outdir>/` (`state/raw/`, `state/graph/`, `state/units/`,
`output/process-docs/`, `output/tables.md`, `migration-tree/`,
`batches.json`). `--patterns` is passed through to the tree step. Prints the next step (review the process with the
`oracle-migration-review` workflow over `batches.json`). Use this instead of
the full run when the estate is large and one process is the unit of work.

### 4.16 `scripts/learn_patterns.py`
`learn_patterns.py --pairs pairs.json [--migrated-dir dir,...]
[--base patterns.json] [--approve auto|all] --out patterns.json`

Learn migration patterns from already-migrated processes. Inputs:
`pairs.json` = `[{"onprem": path, "migrated": path, "approved": bool}]`
(`approved: true` = the migrated file was vetted as a correct migration);
`--migrated-dir` = migrated-only code (no on-prem counterpart). Output
`patterns.json` (version 1) with `library_map` (on-prem import → cloud
import), `call_map` (on-prem call → cloud call, or `to: null` = dropped,
e.g. `Connection.commit` has no equivalent), `sql_overrides`
(statement-level Oracle → Spark rewrites the mechanical translator did NOT
produce), and `cloud_idioms` (frequency counts of imports/calls in migrated
code). Derivation is deterministic diffing: removed vs added imports per
pair (method-name and receiver.method call shapes from a light
comment/string-stripped scan); SQL overrides come from running
`translate()` on each on-prem statement and keeping the ones whose migrated
counterpart differs (case-insensitive compare, so formatting alone is not an
override). Pairing a removed import with an added one is a semantic
judgment, so derived entries start as `proposed` with `evidence` (pair IDs)
and `count`. They become `approved` only via `--approve auto` (unambiguous:
exactly one removed + one added import in the pair, or the same pairing in
≥3 pairs; vetted SQL overrides) or `--approve all` (the user asserts the
exemplars were vetted), or by editing `"status"` in the file. `--base`
merges into an existing `patterns.json` (evidence union, counts bump,
approved entries stay approved) so the library grows as more processes are
migrated.

### 4.17 Patterns in `translate_sql.py` / `prepare_migration_tree.py`
`translate(sql, ignored=None, overrides=None)`: an approved SQL override
whose whitespace-normalized `from` exactly matches the statement wins over
the mechanical rules and returns `applied: ["pattern:<id>"]`,
`confidence: high`, no flags. `prepare_migration_tree.py --patterns
patterns.json`: approved library mappings rewrite `import` lines in place
in Java files (recorded in the bundle as `patterns_applied`); call sites
using the old class name, and approved `call_map` entries (including
`to: null` dropped calls), are flagged in the bundle as `pattern_flags`
with the pattern ID and line numbers — never auto-rewritten, because cloud
APIs differ in shape and the agent rewrites them using the exemplar.
`translation-report.md` gains a patterns section (import rewrites, call-site
flags, approved counts). The review prompt (`prompts/classify_process.md`)
tells the agent to apply bundled patterns by ID and to propose new patterns
(from → to + evidence file) in `migration_notes` when its own migration work
generalizes.

### 4.14 `scripts/ignore_lists.py`
Shared ignore-list handling (stdlib only). An ignore file is JSON:
`{"java_imports": ["^oracle\\.", "^weblogic\\."],
"oracle_objects": ["^DBMS_.*", "^UTL_.*", "^V\\$.*"]}`. Patterns are regexes
matched with `re.search` against the full name (imports as written, Oracle
names uppercased). `IgnoreList` exposes `ignore_import(name)` and
`ignore_object(name)` plus `__bool__`; `load_ignore(path)` returns an empty
list for a missing/empty path; `split_kept_ignored(names, predicate)` returns
`(kept, ignored)` sorted lists. Used by scan_java.py, scan_plsql.py,
build_graph.py (an `apply_ignore` step filters already-scanned records at
load time, so data scanned before the list existed can be re-filtered without
re-scanning) and prepare_migration_tree.py (passed to `translate()`).
A starter file lives at `fixtures/ignore.json`.

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
- `CoreWM/jobs/LegacyJob.java`: `main()` class with **no package declaration**
  (default package), updating `RISK_POSITIONS` via plain JDBC — exercises the
  class-name-only (`maybe`) linkage path
- `scripts/run_nightly.sh`: ksh wrapper that sources an env script, runs
  `java ... com.risk.wm.jobs.NightlyRiskJob $1`, and calls
  `sqlplus ... @/opt/risk/sql/post_load.sql`
- `Risk_DB/sql/post_load.sql`: `INSERT INTO RISK_AUDIT ... SELECT ...
  FROM RISK_RESULTS` — exercises the script→SQL (`write_via_script`) path
- `controlm/jobs.xml`: two `<JOB>` elements, one invoking the nightly class
  via `java ... com.risk.wm.jobs.NightlyRiskJob`, one running a `.sh` script,
  with `INCOND` dependencies
- `autosys/jobs.csv`: the 9-column Autosys layout, including an exact duplicate
  row, two rows for one job with different `CONDITION`s (one a
  `s(A) AND s(B)` expression), a `PARENT_JOB_NAME`, a `WRAPPED_JOB` whose
  command is the wrapper `.sh` (not java directly), and a `LEGACY_JOB` whose
  command references `com.risk.legacy.LegacyJob` (the package-less file)

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
7. `scan_scripts.py` on the fixtures finds the `java
   com.risk.wm.jobs.NightlyRiskJob` invocation, the `risk-dl.jar` reference,
   the sourced `env.sh`, and the `sqlplus @post_load.sql` target.
8. `build_graph.py --scripts` gives `WRAPPED_JOB` linkage `ok` with the
   nightly job's files in its bundle and `RISK_AUDIT` as `write_via_script`;
   gives `LEGACY_JOB` linkage `maybe` with `linkage_candidates =
   ['LegacyJob']`; and creates a `synthetic: true` unit
   `main:com.risk.wm.jobs.RiskReportJob` (not referenced by any Autosys job)
   without duplicating the nightly job.
9. Column extraction: `DbUtil` reports `table_columns.RISK_POSITIONS.write =
   [POSITION_ID, BOOK, EXPOSURE, AS_OF_DATE]`; the DDL file reports
   `ddl_columns.RISK_POSITIONS` with the same four columns; `NightlyRiskJob`
   reports `RISK_RESULTS.read = [TOTAL_EXPOSURE]` and `RISK_POSITIONS.read`
   includes `BOOK` and `EXPOSURE` (from the derived-table subquery).
10. `build_process_docs.py` writes one HTML per unit plus `index.html`; the
    nightly doc contains the DRAFT description, the dependency chain, an
    Inputs table listing RISK_POSITIONS, and an inline `<svg>` diagram.
    `build_table_catalog.py` writes `tables.md` with a `RISK_POSITIONS`
    section listing the DDL columns and the nightly job under both readers
    and writers.
11. `translate_sql.py`: `NVL(x, 0)` → `COALESCE(x, 0)`, `SYSDATE` →
    `current_timestamp()`, `TO_DATE('...','YYYY-MM-DD')` →
    `to_date('...','yyyy-MM-dd')`, `DECODE(...)` → `CASE ... END`, while a
    `ROWNUM` statement returns `confidence: needs-review` with a ROWNUM flag
    and no rewrite. `prepare_migration_tree.py` mirrors the fixture tree,
    rewrites the NVL/SYSDATE literals in `NightlyRiskJob.java` in place,
    appends a `/* TODO-SPARK: ROWNUM ... */` marker to the ROWNUM fragment,
    and writes a bundle per touched file plus `translation-report.md`.
12. Ignore lists: with `--ignore fixtures/ignore.json`, `scan_java.py`
    reports `oracle.jdbc.OracleDriver` under `ignored_imports` (not
    `imports`); `scan_plsql.py` reports `DBMS_OUTPUT.PUT_LINE` under
    `ignored_calls` and `V$SESSION` under `ignored_tables`, with no `dbms`
    entry in `risky_constructs`; `build_graph.py` creates no `db:V$SESSION`
    or `db:DBMS_OUTPUT*` nodes; `translate()` on a `DBMS_OUTPUT` statement
    with the ignore list returns no flags, while a `ROWNUM` statement still
    returns the ROWNUM flag.
13. Without `--ignore`, the same fixtures still trace everything (the
    `DBMS_OUTPUT` call, the `V$SESSION` read, the `oracle.jdbc` import) —
    ignoring is opt-in, never silent.
14. Single-process mode: `run_process.py --process NIGHTLY_RISK_JOB` on the
    fixtures (with `--ignore fixtures/ignore.json`) produces exactly one unit
    in `units.json`, one HTML doc plus `index.html`, a `tables.md` with only
    `RISK_POSITIONS` and `RISK_RESULTS`, a `batches.json` with one batch
    containing only `NIGHTLY_RISK_JOB`, and a migration tree with exactly the
    process's files — `NightlyRiskJob.java`, `DbUtil.java`,
    `risk_tables.sql`, `risk_calc_pkg.pkb`, `post_load.sql`,
    `run_nightly.sh` — with `NVL`/`SYSDATE` translated in place and the
    `ROWNUM` fragment flagged; the unit lists `RISK_AUDIT` as
    `write_via_script` (the wrapper script's `sqlplus` file belongs to the
    process even though the job command calls Java directly).
15. Without `--only`, the same run still produces all 6 units, 6 docs, and
    the full 8-file tree — filtering is opt-in, never silent.
16. Pattern learning: `learn_patterns.py --pairs fixtures/exemplars/pairs.json
    --migrated-dir fixtures/exemplars/migrated --approve auto` writes a
    `patterns.json` with exactly one approved library mapping
    (`com.risk.db.OracleWriter` → `com.bank.cloud.AdlsWriter`), one approved
    call mapping (`commit` → dropped, `to: null`), one approved SQL override
    (the MERGE pair), and `cloud_idioms` listing `com.bank.cloud.AdlsWriter`;
    without `--approve` all three entries are `proposed`.
17. Pattern application: `prepare_migration_tree.py --repos
    ex=fixtures/exemplars/onprem --patterns patterns.json` rewrites the
    `SimpleWriter.java` import to `com.bank.cloud.AdlsWriter`, flags the
    `new OracleWriter(...)` line and the `writer.commit()` line in the bundle
    (call sites are never auto-rewritten), and translates `merge_load.sql` to
    the Spark MERGE with `applied: ["pattern:sql-001"]` and no flags; without
    `--patterns` the import is unchanged and the MERGE is flagged.
18. `--base` merge: running `learn_patterns.py` a second time with
    `--base patterns.json` keeps the same three entries, counts at 1, and
    approved statuses intact — the library grows by evidence, not by
    duplication.

## 7. Definition of done

- Every file exists at the specified path; all scripts are stdlib-only and
  executable via the documented CLI flags.
- All eighteen self-test assertions pass; paste the verification output.
- The review prompt, config, and README are complete and consistent with the
  scripts (flag names, JSON field names, and the checkpoint sanitize rule
  match exactly).
- No LLM is required to run the scan phase; the review phase never re-does a
  checkpointed unit.

Build it now.

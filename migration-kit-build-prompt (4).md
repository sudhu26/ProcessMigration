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
    run_process.py
    learn_patterns.py
    compile_reports.py
    ignore_lists.py
  prompts/
    classify_process.md
  fixtures/
    ignore.json
    exemplars/
    exemplars/onprem/MergeWriter.java
    exemplars/onprem/commented_merge.sql
    exemplars/onprem/nosemi_load.sql
    exemplars/migrated/nosemi_load.sql
    exemplars/pairs-nosemi.json (one pair, separate from pairs.json — see §6)
    edge-cases/EdgeCaseSql.java
    edge-cases/table_func.sql
    edge-cases/FromTables.java
    edge-cases/from_tables.sql
    edge-cases/CommaTables.java
    edge-cases/comma_tables.sql
    edge-cases/DerivedTables.java
    edge-cases/derived_tables.sql
    edge-cases/QualifiedStar.java
    edge-cases/qualified_star.sql
    edge-cases/StarRule.java
    edge-cases/star_rule.sql
    edge-cases/UnionColumns.java
    edge-cases/union_columns.sql
    edge-cases/ArgsJoin.java
    edge-cases/ImportOrder.java
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

- Protect string-literal spans FIRST, then mask block and line comments
  (comment spans are masked only outside string literals, so a `//` inside a
  string literal stays intact), then extract string literals from the
  comment-masked text — a SQL string literal inside a Java comment is
  therefore NOT counted (commented-out SQL is not a live read). Literals are
  `"..."` (with escapes) and `"""..."""` text blocks, matched by ONE regex
  in a single pass over the comment-masked text, so text blocks and `"..."`
  strings share one coordinate space.
- **Merge literals concatenated with `+`** (the standard Java SQL-building
  pattern), allowing whitespace/newlines and at most one simple variable
  expression, optionally with one non-nested bracketed index (`args[0]`) or one
  non-nested parenthesised expression, or a non-nested parenthesised expression on its own,
  between them. The one parenthesised/bracketed expression between fragments is
  non-nested — `+ (f(x)) +` does not merge (it splits into two fragments),
  while `+ (x+y) +` and `+ (suffix) +` do merge. The `between` regex is a
  module-level constant `BETWEEN_RE` in `scan_java.py`, imported by
  `prepare_migration_tree.py` (never forked) — so the tree and the scanner
  merge the same `+`-concatenated chains; the shared import, not a copy, is
  what keeps them identical. The runnable guard is
  `prepare_migration_tree.BETWEEN_RE.pattern == scan_java.BETWEEN_RE.pattern`
  (see §6).
  Joined fragments are concatenated with ` ? `
  inserted at every join (the placeholder for an intervening variable
  expression; it is inserted even when the join is a bare `+` with no
  variable — e.g. a text block followed by `+ " WHERE ..."` merges to
  `... RISK_POSITIONS ? WHERE ...`, see assertion 20).
- Record `package`, `imports` (list, in source order), `classes` (list:
  class/interface/enum/record names).
- Count JDBC signals (regex on comment-stripped AND string-stripped code —
  literals are replaced by `""` first, so a `prepareStatement(` inside a
  string literal is NOT counted): `getConnection`,
  `prepareStatement`, `prepareCall`, `createStatement`, `executeQuery`,
  `executeUpdate`, `executeBatch`, `addBatch`, `setAutoCommit`, `commit`,
  `rollback`, `setSavepoint`. `commit` is zero-arg-only
  (`\bcommit\s*\(\s*\)` — a `commit(x)` call is missed); `setSavepoint`
  also matches capital-`S` `SetSavepoint`. Only nonzero counts are kept.
- A merged literal is a "SQL literal" if it matches
  `\b(SELECT|INSERT\s+INTO|INSERT|UPDATE|DELETE\s+FROM|DELETE|MERGE\s+INTO|MERGE|WITH|TRUNCATE|CALL|CREATE|DROP|ALTER)\b`
  (case-insensitive; longer alternatives first, so `INSERT\s+INTO` is tried
  before bare `INSERT`, `DELETE\s+FROM` before bare `DELETE`, and
  `MERGE\s+INTO` before bare `MERGE`).
- From SQL literals extract, with read/write kinds:
  - read: a `_read_tables()` helper reusing `_from_tables()` extracts the
    reads (and `table_columns` uses the same helper): it covers
    comma-joined tables (`FROM t1, t2`), bare `JOIN`s (`JOIN t2 ON ...`),
    derived tables (`FROM (SELECT ...) d`), `UNION`/`MINUS`/`INTERSECT`
    branches, `TABLE(` (`FROM TABLE(my_func)` reads `MY_FUNC`), and
    non-`SELECT` `FROM` clauses. There is no lookahead: `_from_tables`
    still returns `TABLE`, `_read_tables` skips it, and `_TABLE_FUNC_RE`
    (`\bTABLE\s*\(\s*(name)`) supplies the function name — and it is
    `_from_tables` returning `TABLE` that produces the documented
    `TABLE:{read:['*']}` artifact in `table_columns` (see the KNOWN
    LEFTOVER below).
  - write: `\bINSERT\s+INTO\s+(name)` (the INTO pattern is the
    INSERT-qualified form only — PL/SQL `SELECT ... INTO var` must NOT
    count as a written table), `\bUPDATE\s+(name)\s+SET`,
    `\bMERGE\s+INTO\s+(name)`. A bare table literally named `TABLE`
    (no paren, e.g. `FROM TABLE`) still matches in `_from_tables` /
    `table_columns` (so `SELECT * FROM TABLE` yields `table_columns`
    `{'TABLE': {'read': ['*']}}`), but `reads` is `[]` (`_read_tables`
    skips `TABLE`).
  - Table names: `[A-Za-z_][\w$]*` optionally schema-qualified; uppercase them.
    Skip the pseudo-table `DUAL`: a name is skipped when `name == 'DUAL'` or
    `name.endswith('.DUAL')` (so `SYS.DUAL` is skipped too). DUAL/`*.DUAL`
    is skipped in `extract_table_columns` as well as in reads.
- Stored-procedure calls: `\{\s*call\s+([A-Za-z_][\w$.]*)` inside SQL literals,
  plus `prepareCall("...")` in code. The `prepareCall` extraction runs on
  comment-stripped, literal-intact text (NOT on the literal-stripped code —
  the pattern needs 1+ characters inside the quotes). Contents starting
  with `{` are skipped (the `{call}` form is already extracted from the
  literal itself). Normalization: strip whitespace, `{` and `(` characters,
  uppercase, strip trailing `)` — so `prepareCall("CALL MYPROC()")` yields
  proc `CALLMYPROC`.
- Oracle dialect features — count occurrences **within the SQL literals**,
  keyed exactly as follows (only nonzero entries are kept):

  | Key | Pattern |
  |---|---|
  | `ROWNUM` | `\bROWNUM\b` |
  | `SYSDATE_SYSTIMESTAMP` | `\b(SYSDATE\|SYSTIMESTAMP\|CURRENT_DATE)\b` |
  | `DUAL` | `\bFROM\s+DUAL\b` |
  | `NVL` | `\bNVL[2]?\s*\(` (NVL and NVL2 lumped under one key) |
  | `DECODE` | `\bDECODE\s*\(` |
  | `OLD_JOIN_OP` | `\(\+\)` |
  | `CONNECT_BY` | `\b(CONNECT\s+BY\|START\s+WITH)\b` |
  | `SEQUENCE` | `\bNEXTVAL\b` / `\bCURRVAL\b` |
  | `FOR_UPDATE` | `\bFOR\s+UPDATE\b` |
  | `HINT` | `/\*\+` |
  | `MODEL_CLAUSE` | `\bMODEL\b` |
  | `PIVOT` | `\bPIVOT\b` |
- Metrics: `loc` (non-blank lines of comment-stripped AND string-stripped
  code — literals are replaced by `""` first, so a multi-line text block
  counts as one line), `branches` (count of `if|for|while|switch|catch|case`),
  `methods_rough` (count of matches of `METHOD_RE = \b(public|protected|private|static|\s)+\s*[\w<>\[\]]+\s+(\w+)\s*\([^;{}]*\)\s*(throws\s+[\w,\s]+)?\{` — a method counts iff the whitespace immediately before its return-type token is preceded by a word character (any identifier, modifier, or annotation), or a modifier group (`public` / `protected` / `private` / `static`) is consumed first. So `public void run()`, `public static void main(...)`, `List<String>` returns, `final void f()`, `private synchronized void run()`, `protected final int n()`, `default void m()`, `@Deprecated void annPkg()`, and even `foo void helper2()` (any word token before the return type) ARE counted, while a bare package-private return type at line start (`void helper()`, `String pkgStr()`) is NOT. A `<...>` type-parameter list breaks the match (`public <T> T g()` and `public static <T> T sg()` are NOT counted despite `public`), and a dotted/qualified return type breaks it too (`public com.foo.Bar getBar()` is NOT counted). Interface/abstract methods without a body are NOT counted (no trailing `{`); constructors are NOT counted (no return-type token — `public NightlyRiskJob()` does not match). A class with a constructor + `public void run()` + `void helper()` therefore reports `methods_rough: 1`, not 2), `has_main`
  (boolean: `public static void main(` present).
- Output JSON array, one record per file, with fields:
  `path, repo, package, classes, imports, jdbc{signal:count, only nonzero},
  sql_literal_count, sql_sample (first 10 literals, whitespace-collapsed,
  truncated to 300 chars), tables_read[] (sorted), tables_written[] (sorted),
  procs_called[] (sorted),
  oracle_isms{name:count, only nonzero}, metrics{loc, branches, methods_rough, has_main}`.
- Column extraction (best-effort, per SQL literal): `INSERT INTO t (c1, c2)`
  → write columns; `UPDATE t SET c1=, c2=` → write columns; the `SELECT` list
  → read columns, attributed via `prefix.col` against the FROM/JOIN tables
  and aliases, or to the single FROM table when unambiguous (multi-table bare
  columns go to `columns_unattributed`). Strip `AS alias`, implicit aliases
  (`SUM(x) E`, `BOOK b` — the implicit-alias strip accepts up to three
  segments, so `schema.table.col alias` strips its alias too), and
  `SELECT ... INTO var`. Strip a leading
  `DISTINCT|ALL|UNIQUE` from the select list (or from the first item) before
  alias handling, and add `UNIQUE` (and `DISTINCTROW`) to `_SQL_KW` —
  otherwise the leading keyword is misread as a column name (or the first
  real column as its alias). `*` is recorded as `*`
  ONLY when the outer FROM tables plus the tables that supply the output
  columns of each FROM-clause derived table name exactly one table. A
  derived table's output tables are its FROM-clause tables (recursing into
  derived tables nested in its FROM clause and into UNION/MINUS/INTERSECT
  branches), plus, for each parenthesised subquery in its SELECT list, that
  subquery's output tables (recursively). A parenthesised subquery counts as
  a derived table only where a table reference is expected — following
  `FROM`/`JOIN` or a comma — so one inside an `ON` condition does not count
  (`SELECT * FROM t1 JOIN t1 ON t1.a IN (SELECT y FROM t2)` keeps `*` under
  `T1`). Subqueries in WHERE never count, at any depth. With multiple tables
  it is silently dropped (never
  added to `columns_unattributed` either) — this is why assertion 9's `*`
  appears: the outer `*` is over a single derived table whose inner query
  names `RISK_POSITIONS` alone. For example, `SELECT * FROM (SELECT a FROM
  t1 WHERE x IN (SELECT y FROM t2)) x` keeps `*` under `T1` (the WHERE
  subquery's `T2` contributes no output columns), while `SELECT * FROM
  (SELECT (SELECT MAX(c) FROM t2) FROM t1) x` drops `*` (the SELECT-list
  subquery's output table `T2` joins the set, so it names two tables). `extract_table_columns` recurses
  UNION/MINUS/INTERSECT branches like `_read_tables` does, so branch columns
  are attributed; a bare `*` over branches naming more than one table in
  total is dropped.
  A leading `WITH name AS (query) [, …]` defines CTEs (`WITH RECURSIVE` and
  CTE column lists included): CTE names are never reported as tables in
  `reads`/`table_columns`; a reference to a CTE resolves against the tables
  its inner query reads — a CTE over exactly one table attributes that table
  (including `*` and `cte.col`), a CTE over several tables leaves bare
  columns unattributed and drops `*`. CTE resolution maps a CTE to its inner
  query's tables, not to its output columns.
  In `alias_map`, the unqualified last segment of each schema-qualified table
  name is also mapped to the table, unless it collides with another
  table/alias, in which case the short name is dropped as ambiguous and the
  column stays unattributed. `_col_refs` parses up to three segments: a
  `schema.table.col` reference is resolved through `alias_map` (full name,
  then short name) and only the column is recorded; genuinely unknown
  prefixes stay unattributed.
  Parenthesised subqueries matching `\(\s*(?:SELECT|WITH)\b` (and an
  optional alias following each) are stripped from the select list before
  `_col_refs`; their columns are attributed by the subquery recursion.
  `SELECT ... INTO` strips the full comma-separated target list (`INTO x, y`,
  each target optionally dotted).
  When a statement names no tables at all (DUAL-only), unattributed columns
  are dropped rather than reported.
  `TABLE_PATTERNS`/`REF_PATTERNS` carry write patterns only; reads come only
  from `_read_tables` (R5-G6).
  A bare column selected from a derived table whose output columns are unknown
  stays in `columns_unattributed` (e.g. `SELECT a FROM (SELECT b FROM t1
  WHERE b>0) x` → `columns_unattributed ['A']`). The terminator scan in `_top_level_from`
  is depth-aware: terminators inside a derived table at depth > 0 do not
  truncate the outer FROM clause, and `_from_tables` registers only
  depth-0 FROM/JOIN tables. `prefix.*` in a SELECT list is recorded as `*`
  under that prefix's table, never as a bare column.
  KNOWN LEFTOVER (do not "fix" by special-casing; describe it as-is):
  any columns selected from `TABLE(...)` are recorded under a bogus `TABLE`
  entry (e.g. `Y`, or `*` for `SELECT *`) — the same depth-blind scan sees
  `FROM TABLE` as a table named `TABLE` with that select list. So
  `table_columns` records `TABLE: {read: ['*']}` for
  `SELECT * FROM TABLE(my_func)`, and `TABLE: {read: ['Y']}` for
  `SELECT a FROM t1 WHERE x IN (SELECT y FROM TABLE(my_func))`. `columns_unattributed` entries are
  bare names for multi-table columns and `PREFIX.NAME` for unknown prefixes.
  Ordering: `table_columns` read/write values are sorted;
  `columns_unattributed` is sorted.
  The FROM-table scan registers every table in the FROM clause:
  comma-separated tables after the first (`FROM t1, t2`) and every `JOIN`ed
  table, including a bare `JOIN ... ON` with no `AS` aliases — a clause
  keyword (`JOIN` / `LEFT` / `RIGHT` / `ON` / `WHERE` / ...) is never
  consumed as a table's alias. Comma followers are registered after an `ON`
  clause and after a derived table too. So for `SELECT a, b FROM t1, t2`, `a` and
  `b` are multi-table bare columns and go to `columns_unattributed`, and
  for `SELECT t2.c, t1.d FROM t1, t2`, `T2.C` and `T1.D` are attributed to
  their tables (see assertion 30).
  Output fields: `table_columns{t:{read:[], write:[]}}`,
  `columns_unattributed[]`.
- Ignore lists (see 4.19): with `--ignore`, imports matching `java_imports`
  patterns and tables/procs matching `oracle_objects` patterns are excluded
  from `imports`/`tables_read`/`tables_written`/`procs_called`/`table_columns`
  and recorded instead in `ignored_imports[]`, `ignored_tables[]`,
  `ignored_procs[]`, `ignored_table_columns{}`. `ignored_tables` is the
  union of ignored reads and ignored writes. `columns_unattributed` IS
  filtered by the ignore list (`PREFIX.NAME` is filtered on the prefix, a
  bare name on the name itself). `imports` preserves source order with and
  without `--ignore` (the ignore partition is order-preserving, not
  sorted); tables/procs are sorted.

### 4.2 `scripts/scan_plsql.py`
`scan_plsql.py --root <dir> --repo <name> --exts ".sql,.pks,.pkb,..." --out <file.json> [--ignore ignore.json]`

`--exts` is REQUIRED (no default). Walk `<dir>` for the given extensions
(case-insensitive). For each file:

- Strip `--` line comments and `/* */` block comments; also build a
  no-string-literals copy (replace `'...'` handling `''` escapes) for reference
  extraction. Definitions, references, calls, risky constructs and DDL
  columns are all extracted from the no-string-literals copy (only
  `metrics.loc` uses string-intact code).
- Definitions: regex for
  `CREATE [OR REPLACE] [FORCE|EDITIONABLE|NONEDITIONABLE] (TABLE|VIEW|
  MATERIALIZED VIEW|PACKAGE BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|SEQUENCE|
  SYNONYM|TYPE|INDEX) [IF NOT EXISTS] [schema.]name` (quoted identifiers are
  also accepted) → `{kind, name}` (name uppercased, schema-qualified when
  present).
- References with kinds: a `_read_tables()` helper reusing `_from_tables()`
  (the same helper as scan_java.py, see 4.1) extracts reads — comma-joined
  tables, bare `JOIN`s, derived tables, `UNION`/`MINUS`/`INTERSECT`
  branches, `TABLE(`, non-`SELECT` `FROM` — and `table_columns` uses it
  too. INSERT INTO / UPDATE..SET / MERGE INTO → `write`. Uppercase names.
  `TABLE` never appears in `reads` (`table_columns` may still record the
  bogus `TABLE: {read: ['*']}` leftover documented in 4.1). DUAL/`*.DUAL`
  is skipped in `extract_table_columns` as well as in reads.
- Calls: `\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\s*\(` (note: the leading
  character class is `[A-Za-z_]`, not `\w`) → `PKG.PROC` (uppercase); also
  `^\s*(EXEC|EXECUTE)\s+(name)`. **Exclude** `EXECUTE IMMEDIATE` from calls
  (track it under risky constructs instead) — the filter also discards the
  literal call `EXECUTE.IMMEDIATE` and any call ending in `.IMMEDIATE`.
- Risky constructs (count, only nonzero entries kept):
  `execute_immediate` (`EXECUTE IMMEDIATE`), `dbms` (`DBMS_*`),
  `utl` (`UTL_*`), `autonomous_txn` (`AUTONOMOUS_TRANSACTION`),
  `bulk_collect` (`BULK COLLECT`), `forall` (`FORALL`), `goto` (`GOTO`).
- Columns: `ddl_columns{t:[cols]}` from `CREATE TABLE` column lists (skip
  CONSTRAINT/PRIMARY/FOREIGN/UNIQUE/CHECK/KEY items) — the table-name pattern
  allows an optional leading `"` (a quoted `CREATE TABLE "NAME" (...)`
  records its columns, like the unquoted form) — `ddl_columns` values
  preserve DDL order; `table_columns` and `columns_unattributed` with the
  same rules as scan_java.py (including the sorted `table_columns` values),
  applied per `;`-separated statement (including comma-joined and bare-`JOIN`
  tables — see 4.1).
- Ignore lists (see 4.19): with `--ignore`, reads/writes/calls matching
  `oracle_objects` patterns are excluded from `reads`/`writes`/`references`/
  `calls`/`ddl_columns`/`table_columns`/`columns_unattributed` and recorded in `ignored_tables[]` /
  `ignored_calls[]` / `ignored_table_columns{t:{read:[], write:[]}}`
  (`ignored_table_columns` is always present, sorted, `{}` when empty);
  `columns_unattributed` is filtered by the ignore list the same way as in
  scan_java.py (`PREFIX.NAME` is filtered on the prefix, a bare name on the
  name itself);
  the `dbms`/`utl` risky-construct counts only count non-ignored matches.
  Definitions are never ignored.
- `metrics.loc` is the count of non-blank lines of comment-stripped,
  string-intact code (unlike scan_java.py, strings are NOT stripped before
  counting `loc` here).
- Output per file: `path, repo, definitions[], references[] (union, sorted),
  reads[], writes[], ignored_tables[], ignored_calls[], ignored_table_columns{},
  ddl_columns{}, table_columns{}, columns_unattributed[],
  calls[] (sorted), risky_constructs{name:count, only nonzero},
  metrics{loc, def_count}`.

### 4.3 `scripts/scan_jobs.py`
`scan_jobs.py --export <file> --out <file.json> [--format auto|controlm|autosys]`

Parse scheduler exports into job records. Auto-detect: `.xml` → controlm;
otherwise sniff the CSV header — a `JOB_ID` column means autosys, else controlm.

**Control-M XML:** tolerant parse — find elements whose tag ends in `JOB`
after namespace-strip and upper-casing (`tag.endswith('JOB')`,
case-insensitive); read attributes `JOBNAME|NAME|JOB_NAME` (name),
`CMDLINE|COMMAND|CMD|CMDLINE_TEXT` (command), `NODEID|HOST|NODE` (host),
`SCHEDULE|DAYS` (schedule), `DESCRIPTION`; descendant elements (not just
direct children) whose tag is `INCOND` — also `IN_COND` and `DEPENDENCY` —
are dependencies, taking each element's `NAME` attribute, falling back to
`COND` then `JOB` when `NAME` is absent. `depends_on` is deduplicated and
sorted (`sorted(set(...))`) — unlike Autosys, where order is preserved.

**Control-M CSV:** columns `job_name, command, depends_on, schedule`
(`depends_on` semicolon-separated). Header matching is case-insensitive
(keys are upper-cased, like Autosys — an uppercase-header Control-M CSV must
not yield `job_name='UNKNOWN'`). `depends_on` is split on `;` and kept in
file order with duplicates preserved (no dedup, no sorting — unlike the XML
path, which emits `sorted(set(...))`).

**Control-M records emit all 13 output fields**, with empty defaults where
the format has none: `job_id` `''`, `description` from the XML `DESCRIPTION`
attribute (or the CSV `DESCRIPTION` column when present), `parent_jobs` `[]`,
`task_name` `''`, `parameters` `''`. The CSV parser also reads
`JOB_ID` / `DESCRIPTION` / `PARENT_JOB_NAME` (`;`-split) / `TASK_NAME` /
`PARAMETERS` when those columns exist.

**Autosys CSV:** columns
`JOB_ID,JOB_NAME,JOB_DESCRIPTION,COMMAND,CONDITION,PARENT_JOB_NAME,TASK_NAME,PARAMETERS`.
- The sheet has **one row per condition** and may contain duplicate rows:
  drop exact-duplicate rows (report the count), group rows by `JOB_NAME`
  (fall back to `JOB_ID` when the name is blank), aggregate distinct
  `CONDITION` values into `depends_on`, collect distinct `PARENT_JOB_NAME`
  into `parent_jobs`, take the first non-blank value for scalar fields, and
  **warn** when one job has conflicting `COMMAND` values. The conflicting-
  `COMMAND` winner is `sorted(set)[0]` — alphabetically first, NOT
  first-in-file (the warning text says "using the first").
- Parse each `CONDITION` cell into job names: extract
  `s(?:uccess)?\s*\(\s*([^)]+?)\s*\)` groups (case-insensitive — this handles
  uppercase `SUCCESS(...)` and a space before the paren, `s (NAME)`) first;
  if none, split on `AND`/`OR`/`;` and strip quotes/parens. The fallback
  split path strips inner spaces (`( FEED )` → `FEED`). Dedupe while
  preserving order.
- `dissect_command(cmd)`: extract a Java main class —
  first try `java <jvm-flags> <com.foo.Bar>` (this path has NO exclusions);
  else the first fully-qualified name with 2+ dots ending in an uppercase
  segment, SKIPPING candidates with a dot-segment exactly equal to `jar`
  (`all(seg != 'jar' for seg in cand.split('.'))`) or starting with `java.` /
  `javax.` / `org.`; plus `*.sh|ksh|bash|bat|cmd|ps1` script paths and `*.jar`
  paths (`*.jar` paths match `[\w./\\:-]+\.jar(?![\w.])` — `.jar` must not be
  followed by `.` or an identifier character, so a package segment literally
  named `jar` (`java com.foo.jar.Bar`) yields no phantom jar). If `COMMAND` yields no linkage, retry dissection on
  `TASK_NAME + PARAMETERS` — but ONLY when BOTH `main_class` and `scripts`
  are empty (jars alone do not count as linkage), and the retry MERGES
  key-by-key into the `COMMAND` result (it keeps a non-empty existing value
  and only takes the retry's value where the `COMMAND` result is empty —
  jars found in `COMMAND` are not lost). `-jar <jar>` is terminal: no main
  class is reported after it — a token after `-jar <jar>` is an argument to
  the jar's main(), not the main class. The bare-FQN fallback keeps skipping
  candidates with a dot-segment exactly equal to `jar`, deliberately
  diverging from §4.4: `FQN_RE` matches `my.jar.Main` in `lib/my.jar.Main`,
  so dropping the skip would misread a jar path as a class; a real class in
  a `.jar.` package (`com.foo.jar.Bar`) is therefore still missed from a
  bare-FQN job command.
- Output per job: `job_name, job_id, description, command, host, schedule,
  depends_on[], parent_jobs[], task_name, parameters, main_class, scripts[],
  jars[]`. `scripts` and `jars` are returned sorted and deduplicated
  (`sorted(set(...))`). Print TWO summary lines: the autosys line
  (`autosys: N rows, D exact-duplicate rows dropped, J jobs` — rows/dupes/
  jobs, no linkage count) and the main line
  (`parsed J jobs (L with code linkage) -> <out> [format=<fmt>]` — jobs/
  linkage, no rows/dupes). Control-M prints only the main line. The
  duplicate count is stdout-only, not in the JSON.
- Malformed rows are not handled: `parse_autosys` raises `AttributeError`
  on a ragged CSV row with more fields than the header. Do not add handling
  for this; it is out of scope.

### 4.4 `scripts/scan_scripts.py`
`scan_scripts.py --root <dir> --repo <name> --out <file.json>`

Walk `<dir>` for `*.sh, *.ksh, *.bash, *.pl, *.py`. For each script (full-line
`#` comments stripped):
- Java main classes via the same `java <flags> <com.foo.Bar>` regex as
  `scan_jobs.py` (`java_mains` has no `jar`-segment filter: `JAVA_MAIN_RE`
  anchors on the `java` keyword, so a `.jar` path can never match it, and a
  main in a package like `com.foo.jar.Bar` is reported) — and with the same
  `-jar`-terminal gate as `scan_jobs.py` in `analyze()`, applied per
  command line: a main is accepted only when its match starts before any
  `-jar` token on the same line, so `java -jar app.jar com.foo.Bar`
  reports no main class, while a `java` invocation on a later line still
  reports its main;
  `*.jar` paths (same `\.jar(?![\w.])` guard as 4.3 — a package segment
  literally named `jar` yields no phantom jar); other invoked scripts (`*.sh|ksh|bash|pl|py` — `scripts_called`
  matches any such path anywhere in the script, including inside an `echo`,
  and excludes the script's own name; `source`/`.` sourcing is covered by
  `SOURCE_RE`, which covers only `sh|ksh|bash` sourcing);
  `sqlplus ... @file.sql` targets — `sql_files` come ONLY from lines
  containing the word `sqlplus` (case-insensitive), each such line scanned
  for `@file.sql` (limitation: a `sqlplus` invocation split across lines
  via a continuation is not followed) — echo/quoted lines are not excluded,
  so `echo "sqlplus @x.sql"` yields a phantom `sql_file`; `python script.py` targets via
  `PYTHON_RE = \bpython[23]?(?:\.\d+)*\b(?:\s+-[A-Za-z]\w*)*\s+([\w./\\:-]+\.py)\b`
  — this matches `python -u x.py`, `python3.11 x.py`, `python3 -u x.py`,
  but a flag that takes a value (`python -W ignore x.py`) is still NOT
  matched (documented limitation). A flag-with-value invocation such as
  `python -W ignore x.py` appears in `scripts_called` (which matches `*.py`
  anywhere) but not in `python_files` (whose `PYTHON_RE` only allows flags
  without values). `java_mains`, `jars`, `scripts_called`, `sql_files` and
  `python_files` preserve first-seen order, unlike `scan_jobs.py`, which
  sorts `scripts`/`jars`.
- Output per script: `path, repo, java_mains[], jars[], scripts_called[],
  sql_files[], python_files[]`.

### 4.5 `scripts/build_graph.py`
`build_graph.py [--java <java_all.json>] [--plsql <plsql_all.json>]
[--jobs <jobs.json>] [--scripts <scripts_all.json>] [--ignore ignore.json]
[--only UNIT1,UNIT2] --outdir <dir>`

`--java`, `--plsql` and `--jobs` are all OPTIONAL (default `''` — omitting
one silently degrades the graph instead of failing loudly). `--outdir` is
required.

Inputs are merged scanner outputs (merge the per-repo files with a small
Python snippet). With `--ignore`, an `apply_ignore` step filters the loaded
records first (imports, tables, procs, calls, references, ddl/table columns,
`columns_unattributed`) so data scanned before the list existed is filtered
without re-scanning. `columns_unattributed` is filtered with the same rule
as the scanners: the `PREFIX` of a `PREFIX.NAME` entry, or the bare name
itself, through `ignore_object` — and the same for the script-SQL records'
columns. With `--only`, dependency inference still runs over the full estate, then the
outputs are pruned to the named units: units.json keeps only them, the graph
keeps their 3-hop neighbourhood (scripts, jars, Java files, DB objects,
upstream/downstream jobs), and the catalog keeps only the objects they touch
(a unit touching `PKG.PROC` keeps the `PKG` catalog entry). Per-unit
`likely_depends_on` / `likely_required_by` are pruned to the kept units, and
the printed inferred count is computed AFTER pruning (e.g. `--only
NIGHTLY_RISK_JOB` → 1 unit, both lists `[]`, and the printout says
`0 inferred data-flow dependencies`). Unknown `--only` unit IDs print a
stderr warning: `warning: --only units not found: [...]`. Build:

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
    Synthetic creation takes `classes[:1]` only (a multi-class file with
    two mains yields one synthetic unit, for the first class), and the
    synthetic job dicts omit `job_id` / `task_name` / `parameters`.
    Script-mediated coverage counts: after `script_java` / `script_script`
    are built, BFS each job's own scripts (following `script→script`) and
    add every reached `script_java` FQN to `covered_fqns` before synthetic
    creation — a main invoked only via a wrapper script the job names is
    referenced, not synthetic (a main invoked only by a script no job names
    is genuinely unreferenced and still becomes synthetic; see assertion 42).
  - Reachable files = entry files + import closure, depth-limited to 3.
  - Aggregate metrics (loc, branches, methods, sql_literals, jdbc_sites,
    proc_calls, oracle_ism_total, write-signal count).
  - `db_objects`: `[{name, kind, columns_read[], columns_written[]}]` where
    kind follows this precedence table (use it verbatim — it is the whole
    rule; within a tier write beats read, and for the same direction the
    script tier beats the proc tier):

    | Evidence | kind |
    |---|---|
    | direct write (any other evidence) | `write` (columns_read kept) |
    | direct read + transitive write | `read` + `transitive_write: write_via_script` (if script write) else `write_via_proc` |
    | direct read only | `read` |
    | no direct + via-script write | `write_via_script` |
    | no direct + via-proc write | `write_via_proc` |
    | no direct + via-script read | `read_via_script` |
    | no direct + via-proc read only | `read_via_proc` |

    "Direct" is the unit's own Java files; "transitive" is one hop through
    a called PL/SQL procedure/package (`PKG.PROC`, falling back to `PKG`)
    or through a `sqlplus @file` SQL file. `db_objects` entries may carry
    `transitive_write: write_via_proc|write_via_script` (only on a
    direct-`read` entry). `call` entries carry `columns_read: []` and
    `columns_written: []` (empty lists, so consumers can index the keys).
    Live fixture: the nightly job's `RISK_RESULTS` is kind `read` with
    `columns_read [TOTAL_EXPOSURE]`, `columns_written [AS_OF_DATE, BOOK,
    TOTAL_EXPOSURE]`, `transitive_write write_via_proc`; `RISK_POSITIONS`
    (direct read + direct write) stays `write` with non-empty
    `columns_read`. Corner case: via-script read + via-proc write yields
    `write_via_proc` (the proc write is not dropped). Columns are merged
    from the Java/PLSQL `table_columns` of the unit's files (direct +
    via-script + via-proc — the transitive loop merges `table_columns`
    from the defining PL/SQL record(s) via a `def_table_cols` index keyed
    by definition name, `PKG.PROC` → `PKG` fallback; the nightly
    `RISK_RESULTS` now carries `columns_written [AS_OF_DATE, BOOK,
    TOTAL_EXPOSURE]`, and the nightly `RISK_POSITIONS.columns_read` gains
    `P_ASOF` — a procedure parameter appearing in the package's SELECT
    list, a scanner-heuristic artifact now surfaced via the merge);
    `columns_unattributed[]` is kept on the unit. The db-object catalog
    gains a `columns[]` field (union of `ddl_columns` for that object).
  - Linkage quality per unit: `linkage = 'ok'` (requires `exact_fqns` or
    `script_fqns` — a job whose script resolves by basename but whose Java
    mains aren't in the index gets `'missing'`, not `'ok'`),
    `'maybe'` (class-name-only match — typical when a file has no
    `package` declaration; all candidates listed in `linkage_candidates` for
    the reviewer to confirm), `'missing'` (no code linked at all).
  - Fields: `unit_id, job_name, command, schedule, depends_on[],
    parent_jobs[], entry_points[], java_file_count, java_files[{path, repo,
    classes}], metrics{}, db_objects[], oracle_isms{}, repos[], linkage,
    linkage_candidates[], synthetic, jars[]` (`jars` = basenames from the job
    command plus all reachable scripts). `java_files` is sorted by `path`
    (sorted-by-path, the same convention as `entry_points: sorted(set(...))`);
    the file list is deterministic — two runs on the same inputs produce
    byte-identical `units.json`.
- Script→code edges: `script:` → `java:` (`invokes`), `script:` → `script:`
  (`invokes`), `script:` → `jar:` (`uses`); job commands' jars also get
  `job:` → `jar:` (`uses`) edges. Script→script targets are resolved by
  basename through `resolve_script` (same warning on multiple matches as
  job→script). When one basename matches multiple scanned scripts, the
  builder warns and uses the first.
- **Data-flow inference** (heuristic, for wave ordering when scheduler data is
  thin): if unit A writes a table (kinds `write`, `write_via_proc`,
  `write_via_script` — and a direct-`read` entry with a recorded
  `transitive_write` also counts as a writer of that table, or the edge to
  its readers would be lost) that unit B reads (kinds `read`,
  `read_via_proc`, `read_via_script`), add edge `job:<B>` → `job:<A>` kind
  `likely_depends_on` with `via: [tables]`; exclude self-pairs. Also skip
  pairs where one unit's entry files are a subset of the other's (both
  non-empty): a job and its own wrapper share the same entry files, so
  each writes what the other reads and the heuristic would otherwise infer
  mutual edges — noise for wave ordering that also puts the wrapper in both
  SVG layers of the process doc. A reader lists every same-closure writer
  separately: the skip only removes pairs where the reader and the writer
  share entry files, not writers that share entry files with each other —
  e.g. the synthetic `main:com.risk.wm.jobs.RiskReportJob` has
  `likely_depends_on ['NIGHTLY_RISK_JOB', 'WRAPPED_JOB']`, two entries for
  one code closure. Also set
  per-unit `likely_depends_on[]` and `likely_required_by[]`.
- Write `<outdir>/dependency-graph.json` (`{nodes[], edges[]}`),
  `<outdir>/units.json`, `<outdir>/db-object-catalog.json`
  (`[{object, kind, defined_in[], columns[]}]`). The catalog includes
  referenced-but-undefined objects with `defined_in: []`: kind `TABLE` for
  read/write references, `PROCEDURE` for call references, `UNKNOWN`
  otherwise. On the full fixture the catalog is 8 objects (`RISK_RESULTS`,
  `RISK_AUDIT`, `V$SESSION`, `DBMS_OUTPUT.PUT_LINE`,
  `RISK_CALC_PKG.CALC_EXPOSURE` among them); with `--ignore` it is 6.
  A package member (`PKG.MEMBER`, e.g. `RISK_CALC_PKG.CALC_EXPOSURE`)
  takes the `defined_in` of its owning package when the member itself has
  no definition record: the member is defined inside the package body, so
  its `defined_in` is the package's defining file(s), not `[]`. On the full
  fixture the catalog entry for `RISK_CALC_PKG.CALC_EXPOSURE` is
  `kind: PROCEDURE, defined_in: ['fixtures/Risk_DB/pkg/risk_calc_pkg.pkb']`.
  Print node/edge/unit/inferred counts.

### 4.6 `scripts/classify.py`
`classify.py --units <units.json> [--db-heavy-ratio 1.6] [--java-heavy-ratio 0.6] [--min-evidence 3]`

All three thresholds are optional CLI flags with the defaults shown.
Thresholds are the CLI defaults (`--db-heavy-ratio 1.6`,
`--java-heavy-ratio 0.6`, `--min-evidence 3`); `config.yaml` does not set
them (see 4.9).

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

### 4.7 `scripts/make_batches.py`
`make_batches.py --units <units.json> --checkpoints <state/units> --batch-size 12 [--only-labels NEEDS_REVIEW] [--only UNIT1,UNIT2] [--limit N] --out <batches.json>`

Compute pending units: `sanitize(unit_id)` not among existing checkpoint
basenames, where `sanitize` replaces every character not in `[A-Za-z0-9_.-]`
with `_`. Optional label filter, optional unit-ID filter, and limit. Emit JSON array of arrays (batches
of unit_ids). Print totals. `--batch-size` must be `>= 1`; any other value
exits with a clean error (no traceback).

### 4.8 `prompts/classify_process.md`

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
equivalent, dynamic SQL, ambiguous linkage, evidence gaps); apply bundled
learned patterns by ID and propose new patterns (from → to + evidence file)
in `migration_notes` when its own migration work generalizes (see 4.17);
and return ONLY this JSON (no fences, no commentary):

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
invent objects or behaviors not supported by evidence. The prompt file itself
must name `units.json` as the evidence bundle's source (not just "a JSON
evidence bundle").

### 4.9 `config.yaml`

```yaml
repos:
  risk_db: ""    # path to Risk_DB checkout
  risk_dl: ""    # path to Risk_DL checkout
  corewm:  ""    # path to CoreWM checkout
job_export: ""   # scheduler export: Control-M XML/CSV or Autosys CSV ("" = skip)
plsql_extensions: [".sql", ".pls", ".pks", ".pkb", ".fnc", ".prc", ".trg", ".vw", ".tab", ".seq"]
ignore_file: ""  # ignore.json path ("" = no ignores); see 4.19
patterns_file: ""  # patterns.json path ("" = no learned patterns); see 4.16
output_dir: "output"
state_dir: "state"
analysis:
  # Pre-classification thresholds are NOT set here: the CLI defaults in
  # scripts/classify.py are the source of truth
  # (--db-heavy-ratio 1.6, --java-heavy-ratio 0.6, --min-evidence 3).
  # Pass different values on the classify.py command line to tune them.
workflow:
  concurrency: 4
  batch_size: 12
```

The config file's own comment names the workflow `oracle-migration-review`
(not `oracle-migration-analysis`).

### 4.10 `README.md`

Document: purpose; the 4-step run (1. fill config, 2. run scan scripts in the
given order, 3. LLM review of batches, 4. compile reports with
`compile_reports.py` — Step 4 shows the `compile_reports.py` command from
4.18); running one process with `run_process.py --process <name>` (scan stays
full, outputs filtered); the Autosys CSV contract and dedup/grouping behavior;
the data-flow inference and its heuristic caveats; the
`read|write|read_via_proc|write_via_proc|read_via_script|write_via_script|call`
kinds; and the resume contract below.

### 4.11 `scripts/build_process_docs.py`
`build_process_docs.py --units units.json [--graph dependency-graph.json]
[--java java_all.json] [--verdicts state/units] [--only UNIT1,UNIT2]
--outdir output/process-docs`

One self-contained HTML document per process (`<sanitized unit_id>.html`, plus
`index.html`), inline CSS and inline SVG — no external assets. Each document
shows: badges (pre-classification, linkage, synthetic, review verdict — the
default label is `UNLABELLED` when no preclassification exists);
"What this process does" — the review summary from the verdict checkpoint
when present, otherwise an auto-drafted 2–3 sentence description built from
evidence (entry points, tables read/written, procs called, size) clearly
marked DRAFT; key facts (command, schedule, entry points, repos, jars,
Oracle dialect features, linkage candidates); the dependency chain
(job → script → jar → Java → Oracle) — the jars shown in the dependency
chain and in the SVG "Scripts / jars" layer are the unit's jars per §4.5,
i.e. job-command jars plus jars from reachable scripts, not just the
job→jar graph edges, so a jar that comes only from a wrapper script (e.g.
`risk-dl.jar`, which no job command names) is still shown; Inputs and Outputs tables with columns
(a table counts as input if its kind is a read kind OR it has
`columns_read`, and as output if a write kind OR `columns_written` — the kind
field is the single dominant direction, columns tell the rest); stored
procedures called; upstream dependencies (scheduler `depends_on` vs inferred
`likely_depends_on` with the `via` tables, plus parent jobs); downstream
dependents; Java-to-Java dependencies (imports of entry files, resolved to
owning process via the `--java` records — `render()` takes the FULL units
list (`all_units`, unfiltered by `--only`); `path_to_unit` is built from all
units, so an import owned by a *different* unit resolves to that unit
instead of being mislabeled external/shared — and when a file belongs to
several units (e.g. `DbUtil.java` in both a job and its wrapper), the
CURRENT unit wins: other units populate the map first, then the current
unit's files overwrite it, so the doc never attributes its own file to
another process. When `--only` is given, the *rendered* units' 
`likely_depends_on` / `likely_required_by` are pruned to the selected
units (the same pruning `build_graph.py --only` applies), so the doc —
text sections and SVG alike — reflects the run's scope even when
`--units` is the unfiltered file (as `run_process.py` passes it);
`depends_on` is NOT pruned (scheduler deps are conditions, not units);
`depends_on` may therefore name jobs outside the `--only` scope, and they
are still shown in the Upstream layer and text section — only the
`likely_*` lists are pruned to the selected units;
`all_units` stays unfiltered throughout, so Java-to-Java resolution still
sees every unit — this guarantee holds when `--units` is the unfiltered file
(as `run_process.py` passes it); with an already-filtered `--units` file,
resolution sees only the selected units, and an import owned by an
unselected unit resolves as `(external / shared lib)`. No fixture exercises
this cross-unit resolution (verified by a synthetic probe with hand-built
units: full `--units` resolves the import to the other unit, filtered
`--units` renders `(external / shared lib)` — `build_graph.py`'s import
closure always pulls an imported file into the importing unit's own file
set); Oracle-to-Oracle dependencies
(what each called procedure itself references/calls, looking up both the full
`PKG.PROC` node and the `PKG` node); one inline SVG dependency diagram
(layered: upstream jobs | this job | scripts/jars | Java | Oracle |
downstream jobs). Upstream and Downstream layers are capped at 5 jobs with
`+N more jobs` (like the middle layers); the Scripts/jars layer is capped at
6 items, Java at 6 files, Oracle at 10 objects, each with `+N more …`. The review
section (verdict, migration notes, human review flags, evidence gaps —
unattributed columns are surfaced in the Review section when the unit is
unreviewed: "Not yet reviewed. Unattributed columns ..."); source files.
Escape all dynamic text with `html.escape`. The scripts shown for a
unit are the job's own scripts plus wrapper scripts that invoke the unit's
entry Java files (a job command often calls Java directly while a wrapper
script invokes the same main). `unit_scripts` BFSes over `script→script`
out-edges to a fixpoint (chained wrapper scripts), then applies the
existing reverse-wrapper step — so a `script→script→java` chain shows every
script in the chain.
Without `--graph`, the script layer is dropped from the docs:
`unit_scripts` / `build_svg` derive scripts solely from graph edges, so the
dependency chain and the SVG lose the job→script and script→java edges (the
nightly doc shows `job → jar → Java → Oracle` with no script).
`run_process.py` and the README always pass `--graph`, so the main paths are
unaffected — but the degradation is silent, and `--graph` is optional here.

- SVG direction rule: the SVG Upstream layer is built from the unit's
  `depends_on` ∪ `likely_depends_on` and the Downstream layer from its
  `likely_required_by` — the same lists the text sections use, so the
  diagram cannot contradict the doc in `--only` mode (the pruned graph
  keeps 3-hop neighbourhood edges from pruned units, which the old
  edge-derived layers read). Edges point dependent → dependency, so in
  the full graph these lists correspond to the job's OUT-edges of kinds
  `depends_on` / `likely_depends_on` and its IN-edges of kind
  `likely_depends_on` (see assertion 21). The SVG Java layer shows entry
  points only (basenames of `unit.entry_points` — e.g. the nightly SVG
  shows `NightlyRiskJob.java`, not `DbUtil.java` from its closure). SVG
  node labels are truncated with an ellipsis to fit the box width
  (26 chars, e.g. `main:com.risk.wm.jobs.Ris…`). Exception — the Oracle
  layer: the kind is load-bearing, so it is abbreviated (`w` = write,
  `r` = read, `w_vs` = write_via_script, `w_vp` = write_via_proc,
  `r_vs` = read_via_script, `r_vp` = read_via_proc, `call` = call) and the
  *name* is truncated to leave room for the abbreviated kind, so the kind
  is always visible within 26 chars (e.g. `RISK_AUDIT (w_vs)`,
  `RISK_CALC_PKG.CALC… (call)`); a legend line at the bottom of the SVG
  spells out the abbreviations. All other layers keep the plain 26-char
  truncation rule.
  The SVG draws exactly these edges, in this order: upstream→job,
  job→scripts, job→jars, job→java, script→java, java→db, job→downstream.
  Every box in the Scripts / jars layer has an incoming edge: scripts from
  the job, jars from the job (a jar that comes only from a wrapper script
  still gets a job→jar line). A script→java line is drawn from each script
  box to every Java class the script invokes (graph script→java edges),
  resolved to the Java layer by class basename + `.java`, one line per
  (script box, Java box) pair. On the nightly fixture (full run, no
  `--ignore`) this is 12 boxes and 12 lines (2 upstream→job, 1 job→script,
  1 job→jar, 1 job→java, 1 script→java, 5 java→db, 1 job→downstream).
  Oracle labels: the kind abbreviation is always visible within the
  26-character box width (`RISK_AUDIT (w_vs)`, `RISK_CALC_PKG.CALC… (call)`).
  When two distinct (name, kind) pairs would truncate to the same label, the
  colliding labels are re-rendered with a distinguishing tail: the head is
  shortened to fit the last *t* characters of the name, with *t* growing
  from 4 until the labels in the group are distinct (head never below 1
  character), so `RISK_POSITIONS_LONG_NAME_ALPHA` and
  `RISK_POSITIONS_LONG_NAME_BETA` (both `write`) render as
  `RISK_POSITIONS_LO…LPHA (w)` and `RISK_POSITIONS_LO…BETA (w)`, and each
  table's java→db edge maps to its own box. Labels that do not collide are
  unchanged. The same tail-distinguishing re-render applies to every layer,
  not only Oracle: when two labels in any layer would truncate to the same
  string, they are re-rendered with a distinguishing tail (head shortened to
  fit the last *t* characters of the name, *t* growing from 4 until the
  labels in the group are distinct), so each edge maps to its own box.
  Edges are additionally keyed by (layer, full item identity), not by
  truncated label: `find()` resolves the full item through the display map
  recorded for its layer, so even if two labels were identical the lines
  would still target their own boxes.

### 4.12 `scripts/build_table_catalog.py`
`build_table_catalog.py --units units.json [--plsql plsql_all.json]
[--only UNIT1,UNIT2] --out tables.md`

`--plsql` is OPTIONAL (default `''`) — omitting it silently degrades every
column list to "observed in use" instead of failing loudly.

One Markdown file, one section per table: columns from `CREATE TABLE` DDL
(authoritative) or, when no DDL exists, columns observed in use; "Read by
(inputs)" — processes reading it, with columns read; "Written by (outputs)" —
processes writing it, with columns written. A table counts as read/written by
a process when its kind is a read/write kind OR it has read/write columns.
A summary table up front lists each table with DDL column count, reader/writer
counts, and whether it is defined in the repos (flag tables referenced but
never defined). The header sentence reads: "Columns from `CREATE TABLE` DDL
are authoritative; otherwise the columns observed in use are listed." (No
column is ever marked with `*` — the old sentence claiming `*` marking is
removed.) With `--only`, `ddl_cols` is intersected with the selected units'
tables, so a table with DDL columns but untouched by the selected units gets
no section (e.g. `--only main:com.risk.wm.jobs.RiskReportJob` → `tables.md`
has only `RISK_RESULTS`). DDL columns are listed sorted in `tables.md`
(the scan-level `ddl_columns` in 4.2 preserve DDL order; the catalog sorts
them for display).

### 4.13 `scripts/translate_sql.py`
Deterministic first-pass Oracle → Spark SQL, stdlib only.
`translate(sql, ignored=None, overrides=None)` returns `{original, translated, applied[],
flags[], confidence}`. There is also a `translate_statement(sql, ignored=None,
overrides=None)` wrapper (same behavior, original preserved for the caller).
`ignored` is an `ignore_lists.IgnoreList`: calls to ignored Oracle packages
are not flagged (they are out of migration scope), while other flags (ROWNUM,
CONNECT BY, …) still apply. `overrides` is a list of approved learned SQL
overrides from `patterns.json`: a whitespace-normalized AND case-insensitive
exact statement match (both sides lowercased) wins over the mechanical rules
and returns `applied: ["pattern:<id>"]`, `confidence: high`, no flags —
where the match is computed WITHOUT the statement terminator: a single
trailing `;` (and surrounding whitespace) is stripped from both the
statement and the override's `from` before comparing, so a Java string
literal without a trailing `;` still matches a learned override whose
`from` ends with `;`. The stored `to` is returned as-is; the mechanical
path keeps the statement (including any terminator) intact.
Mechanical local patterns only. Keyword rules are applied outside string
literals and comments; function rewrites (`NVL`, `NVL2`, `TO_DATE`,
`TO_CHAR`, `TRUNC`, `DECODE` via `_replace_calls` / `_find_calls`) are
scoped the same way: they take protected spans (string literals + comments)
and skip calls inside them, the paren scan is string-aware, and spans are
recomputed before each function (a rewrite changes the offsets of everything
after it) — so `SELECT 'NVL(x,1)' FROM t` is unchanged with `applied: []`,
a mixed `NVL(a,'x,y'),'NVL(b,2)'` rewrites only the real call, and an
`NVL` inside a comment is untouched. Rules:
`NVL(x,y)`→`COALESCE(x,y)`,
`NVL2(x,y,z)`→`CASE WHEN x IS NOT NULL THEN y ELSE z END`,
`SYSDATE`/`SYSTIMESTAMP`→`current_timestamp()`, `FROM DUAL` removed,
(a `SYSDATE` rewrite records `SYSDATE->current_timestamp()` and a
`SYSTIMESTAMP` rewrite records `SYSTIMESTAMP->current_timestamp()` — both
names when both fired),
`/*+ */` hints removed — hint removal acts on comment segments: a segment
starting with `/*+` is stripped and `optimizer hint removed` recorded
(a hint inside a string literal survives unchanged; see assertion 22),
`TO_DATE`→`to_date` and `TO_CHAR`→`date_format`
with Oracle→Spark format-mask mapping (`YYYY`→`yyyy`, `YY`→`yy`, `MM`→`MM`,
`DD`→`dd`, `HH24`→`HH`, `HH12`→`hh`, `MI`→`mm`, `SS`→`ss`, `MON`→`MMM`,
`MONTH`→`MMMM`, `DY`→`EEE`, `DAY`→`EEEE`; unmapped mask tokens flag the call
and leave it unchanged). 1-arg forms also rewrite: `TO_DATE(x)`→`to_date(x)`
(applied `TO_DATE->to_date`) and `TO_CHAR(x)`→`CAST(x AS STRING)` (applied
`TO_CHAR->CAST` — the 2-arg form records `TO_CHAR->date_format`).
`TRUNC(x)`/`TRUNC(x,'DD')`→`to_date(x)`, `TRUNC(x,'D')`→`to_date(x)`,
`TRUNC(x,'MM'|'MON'|'MONTH')`→`date_trunc('month', x)`,
`TRUNC(x,'YYYY'|'YY'|'YEAR')`→`date_trunc('year', x)`,
`TRUNC(x,'HH'|'HH24')`→`date_trunc('hour', x)`, `DECODE(a,v1,r1,...,default)`→
`CASE a WHEN v1 THEN r1 ... [ELSE default] END` (paren-aware AND
string-aware arg splitting — `''` escapes handled — so
`DECODE(a,'x,y',1,0)` → `CASE a WHEN 'x,y' THEN 1 ELSE 0 END`).
Flagged, never guessed: `ROWNUM` (needs `ROW_NUMBER() OVER (ORDER BY ...)`),
`CONNECT BY`/`START WITH` (recursive CTE), `(+)` joins, `NEXTVAL`/`CURRVAL`,
`FOR UPDATE`, `EXECUTE IMMEDIATE`, `MODEL`, `PIVOT`, `DBMS_`/`UTL_` calls,
`MERGE` (review), `BULK COLLECT`/`FORALL`. Confidence: `needs-review` if any
flags, `medium` if `DECODE->CASE` applied or any applied rule name contains
`->to_date`, `->date_format`, or `->CAST` (so `TO_DATE` → medium, `TO_CHAR` →
medium in both forms, and `TRUNC->date_trunc/to_date` stays high — it
contains none of those substrings), else `high`. Statement splitting is
string/comment-aware: `split_statements()` splits only on `;` outside
string literals and `--` / `/* */` comments (reusing the protected spans
used for function rewrites), so a `;` inside `'a;b'` or inside a comment
does not split a statement.

### 4.14 `scripts/prepare_migration_tree.py`
`prepare_migration_tree.py --repos Risk_DB=<path>,Risk_DL=<path>,CoreWM=<path>
--outdir migration-tree [--ignore ignore.json] [--patterns patterns.json]
[--only UNIT1,UNIT2 --units units.json --graph dependency-graph.json
--catalog db-object-catalog.json]`

Build the migration working tree: mirror each repo's folder/file structure
**exactly** (same relative paths) under `<outdir>/<repo>/`. Java files are
copied as-is except SQL string literals: detect `+`-concatenated literal
chains (same `between` rule as scan_java.py — a chain is SQL when its merged
content matches the SQL-keyword pattern). The `between` regex is the shared
module-level constant `BETWEEN_RE` defined in `scan_java.py` and imported by
`prepare_migration_tree.py` (never forked) — the two must stay identical, so
the tree and the scanner merge the same `+`-concatenated chains (see §4.1). The shared `_split_leading_comments()` helper lives in
`translate_sql.py` and is imported by `prepare_migration_tree.py` — do not
fork it. Translate each fragment with
`translate_sql.py`, apply high/medium translations in place (re-escaping
string literals), and leave needs-review fragments unchanged with a
`/* TODO-SPARK: <flags> */` marker after the literal. The merged
`chain_sql` recorded in each bundle item is truncated to 1000 chars.
PL/SQL files get the first-pass translation applied statement-by-statement
plus a header comment listing applied rules and flag count. PL/SQL files
are split into statements with the string/comment-aware
`split_statements()` from `translate_sql.py` (not a bare
`re.split(r'(?<=;)')`), so a `;` inside a string literal or a comment does
not split a statement. The mirrored
file body is rebuilt by joining the per-statement `translate()` results
computed for the bundle (one result per `;`-terminated statement, in file
order) — never by one whole-file `translate()` call, which cannot match a
statement-level approved SQL override in a multi-statement file (the
override match in 4.13/4.17 is per statement). `CREATE TABLE` statements
and statements matching neither the SQL-keyword pattern nor any flag pattern
are restored verbatim in the same pass. Each translated statement keeps its
surrounding whitespace (the newline after `;` belongs to the next chunk), so
statements stay on separate lines; an approved override's replacement text
is whitespace-normalized (see 4.17), so an overridden statement is a single
line in the mirrored body. One terminator rule (shared with §§4.13/4.17):
stored `from`/`to` retain their terminators; only the compare strips a
single trailing `;` (and surrounding whitespace) from both sides (see
§4.13). The terminator is preserved from the original chunk in both
directions: in `translate_plsql_file()`, if the original chunk ends with
`;` and the replacement text doesn't, `;` is re-appended before the trailing
whitespace; if the original chunk lacks `;` and the replacement ends with
one, the trailing `;` is stripped from the replacement. The mirrored Java literal contains the stored `to` as-is — a
mirrored Java literal can gain a trailing `;` inside the string when the
stored `to` ends with one (the stored `to` is returned as-is); whether that
trailing `;` is safe for the target execution path is the agent's check,
since the original literal had none and executed fine.
Before calling `translate()`, `translate_plsql_file()`
strips a leading `--…` / `/*…*/` comment prefix from the chunk for the
override compare, while the comment is preserved verbatim at the head of
the mirrored body. Per statement, the
translator runs on statements matching the SQL-keyword pattern OR any flag
pattern from `translate_sql.py` (`_FLAG_PATTERNS`) — a flag-only procedural
statement such as `EXECUTE IMMEDIATE ...;` or `DBMS_OUTPUT.PUT_LINE(...);`
carries no SQL keyword but must still reach the header, the bundle and the
report, or its flags are silently dropped. `translate()` itself only fires
flags outside string literals and comments, so a flag-pattern hit inside a
literal yields no item. `CREATE TABLE`
statements are skipped (leading comments/whitespace tolerated) and restored
verbatim — `CREATE TABLE` DDL is copied as-is, not translated, so
`CREATE TABLE ... DEFAULT SYSDATE` is NOT rewritten. `CREATE VIEW`
statements are translated like any other statement (only `CREATE TABLE` is
copied as-is) — a `CREATE VIEW ... SELECT SYSDATE` IS rewritten. The PL/SQL
header comment is always emitted, even when `items` is empty — but its
bundle sentences (`Still needs agent work: N flag(s) — see bundle.` and
`Original Oracle code is preserved in the translation bundle.`) appear only
when a bundle will be written for the file, i.e. when at least one
statement reached `translate()`; a file with no items gets a header ending
`Still needs agent work: 0 flag(s).` with no bundle sentences and no bundle
file. The header's flag
count — and the report's `Literals/statements flagged for agent review`
count for PL/SQL — is the number of needs-review statements (`confidence
== 'needs-review'`), exactly as for Java literals: two statements sharing
one flag message count as 2, not 1. (The bundle's top-level `flags` list is
still the sorted distinct flag messages.) The PL/SQL bundle lists EVERY
statement that reached `translate()` — including no-ops, recorded with
`applied: []`, `flags: []`, `confidence: high` — not only changed/flagged
ones. Statements matching neither the SQL-keyword pattern nor any flag
pattern (e.g. `COMMIT;`) never reach `translate()` and are not listed.
PL/SQL statement
originals/translations in bundles are truncated to 2000 chars. All other
files are copied byte-for-byte. One translation bundle per touched file at
`translation-bundles/<repo>/<relpath>.translate.json` (every literal/statement
with original, translated, applied rules, flags, confidence, and the merged
`chain_sql` for concatenated fragments) — a bundle is written ONLY for a
file with items/flags (Java: items or pattern applies/flags; PL/SQL: items
or flags), so a PL/SQL file with no items/flags gets the header comment but
NO bundle file and no bundle sentences in the header.
(`risk_tables.sql` now DOES get a bundle: its `CREATE VIEW` statement is
recorded as a no-op, so it is no longer an example of a file with a header
but no bundle.) PL/SQL bundles carry a
top-level `patterns_applied`: sorted list of bare approved-override IDs
(e.g. `["sql-001"]`, `[]` when none). Write `translation-report.md`
(file counts, literals translated/flagged, list of files needing agent review).
A file is listed under "Files with items needing agent review" when its
bundle contains a needs-review literal/statement (any flagged item) or any
`pattern_flags` — a file listed solely for pattern flags (e.g.
`ex/SimpleWriter.java` in the patterns tree run) is listed too.
The report line is exactly
`- Literals/statements translated by the first pass: N`
(Java literals + PL/SQL statements; the full fixture tree counts 3). The
report gains a `## Limits of this first pass` section with exactly these
lines:
- Patterns split across concatenated SQL fragments are left for the agent.
  (Mechanical rewrites split across concatenated fragments are left for the
  agent too: per-fragment translation means a call split across fragments
  (e.g. `TO_DATE(' + asof + ', 'YYYY-MM-DD')` in the ROWNUM chain of
  NightlyRiskJob.java) is not rewritten even though the merged `chain_sql`
  is complete.)
- `CREATE TABLE` DDL is copied as-is, not translated.
With `--ignore`, calls to ignored packages are not flagged for conversion.
With `--patterns patterns.json`, approved library mappings rewrite imports in
place, approved SQL overrides replace the mechanical rules (see 4.17), and
call sites using the old libraries are flagged in the bundle with the
pattern ID (never auto-rewritten); the report gains a patterns section. A
missing `--patterns` file is silently treated as `{}` with no warning. With
`--only`, mirror only the selected units' files: their Java files, the PL/SQL
files defining the objects they touch (catalog `defined_in`), and the scripts
and SQL files reachable from their jobs in the graph (3 undirected hops —
a wrapper script often reaches the job only via the Java main it invokes).
`--only` without `--units` / `--graph` / `--catalog` silently mirrors
nothing (only a stderr warning for unknown unit IDs).

### 4.15 `scripts/run_process.py`
`run_process.py --process UNIT1[,UNIT2] --repos Risk_DB=<path>,... [--jobs
<export>] [--ignore ignore.json] [--patterns patterns.json]
[--plsql-exts <list>] [--outdir runs/<first-process>]`

`--jobs` is OPTIONAL (when absent an empty `jobs.json` is written);
`--plsql-exts` defaults to `.sql,.pks,.pkb,.fnc,.prc,.trg,.vw,.tab,.seq`;
the default outdir is `runs/<first-process>` with `:` replaced by `_`
(needed for `main:...` units).

One-command pipeline for a single process (or a few): runs the full scan over
the repos (the scan always covers the whole estate — dependency inference
needs it), then `build_graph.py` twice — once unfiltered into
`state/graph-full/` (its `units.json` is the FULL list
`build_process_docs.py` resolves Java-to-Java owners against, per 4.11, so
an import owned by a different, unselected unit still resolves instead of
being mislabeled external/shared) and once `--only` into `state/graph/` for
the filtered outputs — `classify.py` on both, `build_process_docs.py --only`
(passed the unfiltered, classified `state/graph-full/units.json`),
`build_table_catalog.py --only`,
`prepare_migration_tree.py --only`, `make_batches.py --only`, and
`compile_reports.py` as the final step, writing the four §5 reports to
`<outdir>/output`, all under `<outdir>/` (`state/raw/`, `state/graph/`,
`state/graph-full/`, `state/units/`, `output/process-docs/`, `output/tables.md`,
`output/process-catalog.csv`, `output/classification-report.md`,
`output/oracle-dialect-census.csv`, `output/db-object-catalog.csv`,
`migration-tree/`, `batches.json`). `--patterns` is passed through to the
tree step. Prints the next step (review the process with the
`oracle-migration-review` workflow over `batches.json`) and a closing
message listing the four report files. Use this instead of
the full run when the estate is large and one process is the unit of work.

### 4.16 `scripts/learn_patterns.py`
`learn_patterns.py [--pairs pairs.json] [--migrated-dir dir,...]
[--base patterns.json] [--approve auto|all] --out patterns.json`

`--pairs` is OPTIONAL (default `''`); at least one of `--pairs` /
`--migrated-dir` is required — enforced: a bare run (neither given) exits
non-zero with an error instead of writing an empty `patterns.json`.
`--approve` defaults to `''` (no approval —
this is what assertion 16's "without `--approve` all proposed" relies on).

Learn migration patterns from already-migrated processes. Inputs:
`pairs.json` = `[{"onprem": path, "migrated": path, "approved": bool}]`
(`approved: true` = the migrated file was vetted as a correct migration);
`--migrated-dir` = migrated-only code (no on-prem counterpart). The paths
in `pairs.json` are relative to the current working directory — run from
the kit root (see §6). Output `patterns.json` (version 1) with
`library_map` (on-prem import → cloud import), `call_map` (on-prem call →
cloud call, or `to: null` = dropped, e.g. `Connection.commit` has no
equivalent), `sql_overrides` (statement-level Oracle → Spark rewrites the
mechanical translator did NOT produce), and `cloud_idioms` (frequency
counts of imports/calls in migrated code), plus a `generated_from` block
(`pairs` + `migrated_dirs`) and a `mechanical` field on SQL overrides
(the mechanical translator's output for the on-prem statement, for
comparison). Derivation is deterministic diffing: removed vs added imports
per pair (call shapes come from bare method names only — see 4.17; the
receiver.method shapes are computed but discarded); SQL overrides come
from running `translate()` on each on-prem statement and keeping the ones
whose migrated counterpart differs (case-insensitive compare, so
formatting alone is not an override). When learning SQL overrides in
`analyze_pair()`, strip any leading `--…` / `/*…*/` comment prefix from both
the on-prem statement (stored as `from`) and the migrated statement (stored
as `to`), using the shared `_split_leading_comments()` from
`translate_sql.py` — the same helper `prepare_migration_tree.py` uses before
the override compare. The compare that decides whether a pair is an override
uses the stripped sides too, so a pair whose only difference is the comment
is not a learned override. `split_statements()` is the same
string/comment-aware splitter as in `translate_sql.py`: a `;` inside a
string literal or comment does not split, so paired statements stay whole
when learning overrides. If a pair's files have different
statement counts, the extra statements are ignored (statements are paired
positionally, in order) and `learn_patterns.py` prints `warning: <pair-id>
statement count differs (on-prem N, migrated M); extra statements ignored`
to stderr and records the same message WITHOUT the `warning: ` prefix in a
top-level `warnings` list in
`patterns.json` (empty when no pair mismatches). Pair migrated files feed
`cloud_idioms` too, via a shared `_idioms_for_file()` helper — `--pairs`
alone yields non-empty idioms (fixture: `AdlsWriter: 1`, `write: 1`).
NOTE: when `--migrated-dir` and `--pairs` cover the same files, each file
is counted twice (fixture: `AdlsWriter` 1 → 2). Pairing a removed import
with an added one is a semantic judgment, so derived entries start as
`proposed` with `evidence` (pair IDs) and `count`. Entries also carry the
durable working flags `unambiguous` (library entries: exactly one removed +
one added import in the pair; call entries: exactly one removed call and at
most one added call — zero added = dropped, still unambiguous) and `vetted` (SQL overrides: the
pair was marked approved in `pairs.json`) — the flags are persisted in
`patterns.json` (not stripped at write time), because they are the basis
on which status is re-evaluated on a later `--base` run. They become `approved`
only via `--approve auto` (unambiguous: exactly one removed + one added
import in the pair — or, for a call entry, exactly one removed call and at
most one added call — or the same pairing in ≥3 pairs; vetted SQL overrides)
or `--approve all` (the user asserts the exemplars were vetted), or by
editing `"status"` in the file. The ≥3 rule is implemented by an
`auto_approves(entry, kind)` helper that receives the list name, and its
`count >= 3` branch applies only for `library_map` / `sql_overrides`, never
for `call_map`.
Ambiguous call pairings are dropped entirely — no cross-product entries are
created for `call_map` (unlike `library_map`, where 3 identical ambiguous
pairs yield 4 entries with `count: 3`, all approved with `--approve auto`);
call entries are created only for the unambiguous shapes (exactly one
removed call and at most one added call), so the ≥3 rule never bites for
`call_map`. For SQL, 3 identical pairs yield 1 entry. `merge_list`
extends its dedup index as entries are appended, so repeats within one run
merge instead of duplicating. `--base` merges into an existing
`patterns.json` (evidence union, counts reflect distinct pair IDs — so
re-running the SAME pairs keeps `count` at 1, which is what assertion 18
pins; approved entries stay approved) so the library grows as more
processes are migrated — and status is re-evaluated when the entry is merged
with new evidence in the run: a `proposed` entry CAN become `approved` on a
later run with `--approve auto` when the rule is met (the persisted
`unambiguous`/`vetted` flags are what the re-evaluation reads), and approval is never revoked by a later run without
`--approve`.

### 4.17 Patterns in `translate_sql.py` / `prepare_migration_tree.py`
`translate(sql, ignored=None, overrides=None)`: an approved SQL override
whose whitespace-normalized AND case-insensitive `from` exactly matches the
statement (both sides lowercased, a single trailing `;` stripped from both
sides before comparing) wins over the mechanical rules and returns
`applied: ["pattern:<id>"]`, `confidence: high`, no flags. Stored
`from`/`to` retain their terminators; only the compare strips a single
trailing `;` from both sides (see §4.13). The terminator is preserved from
the original chunk in both directions (see §4.14): a `;` is re-appended when
the original chunk has one and the replacement doesn't, and stripped from
the replacement when the original chunk lacks one. In PL/SQL files the
override compare is made against the statement with any leading `--…` /
`/*…*/` comment stripped; the comment is preserved in the mirrored body.
The leading-comment strip applies at both ends: §4.16 strips the prefix when
learning `from`/`to`, and §4.14 strips it before the compare in
`translate_plsql_file()`; the comment itself is preserved verbatim at the
head of the mirrored body.
`prepare_migration_tree.py --patterns patterns.json`: approved library
mappings rewrite `import` lines in place in Java files (recorded in the
bundle as `patterns_applied`); call sites using the old class name, and
approved `call_map` entries (including `to: null` dropped calls), are
flagged in the bundle as `pattern_flags` with the pattern ID and line
numbers — never auto-rewritten, because cloud APIs differ in shape and the
agent rewrites them using the exemplar. In PL/SQL files the override is
applied per statement when the tree rebuilds the body statement-by-statement
(see 4.14), so an overridden statement in a multi-statement file is
translated in the mirrored body, not only in the bundle — see assertion 28.
`translation-report.md` gains a
patterns section (import rewrites, call-site flags, approved counts). The
review prompt (`prompts/classify_process.md`) tells the agent to apply
bundled patterns by ID and to propose new patterns (from → to + evidence
file) in `migration_notes` when its own migration work generalizes.
Call mappings are derived from bare method names only (receiver info is
discarded); `apply_java_patterns` flags any `.<method>(` line regardless of
receiver, and any line containing the old class name, including comments
and string literals — over-flagging is a known limitation, the agent must
check the receiver.

### 4.18 `scripts/compile_reports.py`
`compile_reports.py --units state/graph/units.json
[--graph state/graph/dependency-graph.json]
[--catalog state/graph/db-object-catalog.json]
[--checkpoints state/units]
[--java state/raw/java_all.json] [--plsql state/raw/plsql_all.json]
--outdir output`

`--units` and `--outdir` are required; every other flag is optional.
`--graph` is optional and only feeds node/edge counts into the
classification report. No LLM, no re-scan: everything is read from files the
earlier steps already wrote. Writes four files into `--outdir`:

- `process-catalog.csv` — one row per process (in `units.json` order), header
  exactly `unit_id,preclassification,verdict,confidence,summary,
  key_db_objects,java_file_count,repos,linkage`. `preclassification` is the
  label, or `UNLABELLED` when the unit has none. `verdict` / `confidence`
  are empty when the unit has no verdict checkpoint. `summary` is the
  verdict summary one-lined (whitespace collapsed) and truncated to 300
  chars. `key_db_objects` is the verdict's `key_db_objects` (object names
  only) semicolon-joined. `repos` is semicolon-joined.
- `classification-report.md` — starts with `{N} processes; {M} reviewed
  ({K} with no verdict file).` and, when `--graph` is given, a
  `Dependency graph: {nodes} nodes, {edges} edges.` line — the line is
  conditioned on the `--graph` argument being set, not on the file loading:
  a missing/empty graph file still yields the line, with 0 nodes, 0 edges. Then: counts by
  pre-classification label (`DB_HEAVY` / `JAVA_HEAVY` / `BALANCED` /
  `NEEDS_REVIEW` / `UNLABELLED` — rows only for labels present); counts by
  review verdict (`DB_HEAVY` / `JAVA_HEAVY` / `BALANCED` / `UNKNOWN`, plus
  a `(no verdict)` row when any unit lacks one); per-class migration-planning
  meanings for `DB_HEAVY` / `JAVA_HEAVY` / `BALANCED` / `NEEDS_REVIEW` /
  `UNKNOWN` (DB-heavy = SQL/procedure conversion effort; Java-heavy = Java /
  data-access translation; BALANCED = both tracks; NEEDS_REVIEW = evidence
  too thin or no code linked, a human must confirm before planning; UNKNOWN
  = reviewer could not decide, treat like NEEDS_REVIEW); the NEEDS_REVIEW
  (pre-classification) list, the UNKNOWN-verdict list, and the no-verdict-
  file list (each `*(none)*` when empty); human-review flags aggregated
  with counts, sorted by count descending then flag name ascending
  (`| *(none)* | 0 |` when none); a shared-library (Risk_DL) note with the
  unit count, the Java file count, the unit IDs, and "migrate the shared
  library first" guidance (shared code is migrated once and reused; changes
  to Risk_DL ripple to every process that imports it, so migrate the shared
  library before — or with the first wave of — its consumers).
- `oracle-dialect-census.csv` — header exactly `feature,occurrences`;
  `oracle_isms` from the Java records plus `risky_constructs` from the
  PL/SQL records, summed, sorted by occurrences descending then feature
  name ascending — code-point (case-sensitive) ascending for the tie-break
  name sort. Without `--java` it falls back to the units' summed
  `oracle_isms` with a stderr note — the fallback counts shared files
  once per unit, so its numbers can exceed the `--java` census (a file in
  two units is counted twice); without `--plsql` it omits the PL/SQL
  side with a stderr note.
- `db-object-catalog.csv` — header exactly
  `object,kind,defined_in,used_by_units`; rows sorted by object — code-point
  (case-sensitive) ascending.
  `defined_in` is semicolon-joined; an empty `defined_in` flags an object
  that is referenced but has no definition in the scanned repos.
  Exception: a package member (`PKG.MEMBER`) shows the owning package's
  defining file(s) in `defined_in` (see §4.5), so an empty `defined_in` on
  a `PKG.MEMBER` row no longer occurs when the package is defined in the
  scanned repos.
  `used_by_units` is semicolon-joined and includes units touching
  `PKG.PROC` under the `PKG` entry. Without `--catalog` it falls back to
  the objects the units touch, with kind `UNKNOWN` and empty `defined_in`,
  plus a stderr note — including a synthesized row for the package of any
  dotted `PKG.PROC` name a unit touches: kind `UNKNOWN`, empty `defined_in`,
  `used_by_units` the units touching `PKG.PROC`. On the full fixture run
  (no `--ignore`) the fallback has 6 objects and includes
  `RISK_CALC_PKG,UNKNOWN,,NIGHTLY_RISK_JOB;WRAPPED_JOB`, matching the
  with-`--catalog` row's `used_by_units`.

Wired into `run_process.py` as the final step, writing to
`<outdir>/output`; its closing message lists the four files. The README
Step 4 (see 4.10) shows the command.

### 4.19 `scripts/ignore_lists.py`
Shared ignore-list handling (stdlib only). An ignore file is JSON:
`{"java_imports": ["^oracle\\.", "^weblogic\\."],
"oracle_objects": ["^DBMS_.*", "^UTL_.*", "^V\\$.*"]}`. Patterns are regexes
matched with `re.search` against the full name (imports as written, Oracle
names uppercased). `IgnoreList` exposes `ignore_import(name)` and
`ignore_object(name)` plus `__bool__`; `load_ignore(path)` returns an empty
`IgnoreList` for a missing/empty path (not an empty list — callers call
methods on the result); `split_kept_ignored(names, predicate)` returns
`(kept, ignored)` sorted lists. There is also a
`split_kept_ignored_ordered(names, predicate)` helper (order-preserving
split, used by scan_java.py for imports). Both pattern lists
(`java_imports` and `oracle_objects`) are compiled with `re.IGNORECASE`.
Used by scan_java.py, scan_plsql.py,
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
4. After review, compile the four reports deterministically with
   `scripts/compile_reports.py` (see 4.18) — no LLM, no re-scan:

   ```
   python3 scripts/compile_reports.py --units state/graph/units.json \
       --graph state/graph/dependency-graph.json \
       --catalog state/graph/db-object-catalog.json \
       --checkpoints state/units --java state/raw/java_all.json \
       --plsql state/raw/plsql_all.json --outdir output
   ```

   - `process-catalog.csv` — `unit_id, preclassification, verdict,
     confidence, summary (≤300 chars, one line), key_db_objects
     (semicolon-joined), java_file_count, repos (semicolon-joined), linkage`
     (preclassification falls back to `UNLABELLED`; verdict/confidence are
     empty when the unit has no verdict file)
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

Create small synthetic fixtures exercising every feature. Run the
`learn_patterns.py` assertions from the kit root: the paths in
`fixtures/exemplars/pairs.json` are relative to the current working
directory, and from any other directory the pairs are skipped with a
`warning: pair-N paths missing, skipped` and empty maps are written.

- `fixtures/ignore.json` — exactly:
  `{"java_imports": ["^oracle\\.", "^weblogic\\."],
  "oracle_objects": ["^DBMS_.*", "^UTL_.*", "^V\\$.*"]}`
- `Risk_DB/ddl/risk_tables.sql`: `CREATE TABLE RISK_POSITIONS (...)`,
  `CREATE OR REPLACE VIEW V_RISK_SUMMARY AS SELECT ... FROM RISK_POSITIONS`
- `Risk_DB/pkg/risk_calc_pkg.pkb`: `CREATE OR REPLACE PACKAGE BODY
  RISK_CALC_PKG` with a procedure that `SELECT SUM...INTO` from
  RISK_POSITIONS, `INSERT INTO RISK_RESULTS ... SELECT ... FROM
  RISK_POSITIONS`, an `EXECUTE IMMEDIATE`, and a `DBMS_OUTPUT.PUT_LINE`.
  The procedure also contains a health check against a system view:
  `SELECT COUNT(*) INTO v_sessions FROM V$SESSION;`
- `Risk_DL/common/com/risk/dl/DbUtil.java`: shared class with a
  `prepareStatement` + `addBatch`/`executeBatch` bulk insert into
  RISK_POSITIONS and a `commit()`
- `CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java`: `main()` class importing
  the shared `DbUtil`; JDBC with `setAutoCommit(false)`/`commit`/`rollback`;
  a `+`-concatenated SQL string using `ROWNUM`; a `{call
  RISK_CALC_PKG.CALC_EXPOSURE(?)}` via `prepareCall`; an `NVL`/`SYSDATE` query.
  It also carries `import oracle.jdbc.OracleDriver;` (the import assertion
  12 depends on).
- `CoreWM/jobs/com/risk/wm/jobs/RiskReportJob.java`: `main()` class reading
  `RISK_RESULTS` with plain JDBC
- `CoreWM/jobs/LegacyJob.java`: `main()` class with **no package declaration**
  (default package), updating `RISK_POSITIONS` via plain JDBC — exercises the
  class-name-only (`maybe`) linkage path
- `scripts/run_nightly.sh`: ksh wrapper that sources an env script, runs
  `java ... com.risk.wm.jobs.NightlyRiskJob $1`, and calls
  `sqlplus ... @/opt/risk/sql/post_load.sql`
- `Risk_DB/sql/post_load.sql` — exactly:
  ```
  -- post-load audit row, run via sqlplus from run_nightly.sh
  INSERT INTO RISK_AUDIT (JOB_NAME, RUN_DATE, STATUS)
  SELECT /*+ FULL(RISK_RESULTS) */ 'NIGHTLY_RISK_JOB', SYSDATE, 'OK' FROM RISK_RESULTS;
  COMMIT;
  ```
  The `/*+ FULL(RISK_RESULTS) */` optimizer hint is the hint-removal case
  (assertion 22).
- `controlm/jobs.xml`: two `<JOB>` elements, one invoking the nightly class
  via `java ... com.risk.wm.jobs.NightlyRiskJob`, one running a `.sh` script,
  with `INCOND` dependencies
- `autosys/jobs.csv`: the 9-column Autosys layout, including an exact duplicate
  row, two rows for one job with different `CONDITION`s (one a
  `s(A) AND s(B)` expression), a `PARENT_JOB_NAME`, a `WRAPPED_JOB` whose
  command is the wrapper `.sh` (not java directly), and a `LEGACY_JOB` whose
  command references `com.risk.legacy.LegacyJob` (the package-less file)
- `fixtures/exemplars/` — pattern-learning exemplars, four files plus
  `pairs.json`:
  - `onprem/SimpleWriter.java`: `package com.risk.jobs;` importing
    `com.risk.db.OracleWriter`; a `run()` method doing
    `OracleWriter writer = new OracleWriter("RISK_RESULTS");`,
    `writer.write("INSERT INTO RISK_RESULTS (AS_OF_DATE) VALUES (SYSDATE)");`,
    `writer.commit();`
  - `migrated/SimpleWriter.java`: same shape importing
    `com.bank.cloud.AdlsWriter`, `new AdlsWriter("risk_results")`,
    `writer.write("INSERT INTO risk_results (as_of_date) VALUES
    (current_timestamp())");` — and NO `commit()` call (the dropped call).
  - `onprem/merge_load.sql`: a `MERGE INTO RISK_RESULTS r USING (SELECT
    BOOK, SUM(EXPOSURE) AS TOTAL_EXPOSURE FROM RISK_POSITIONS GROUP BY BOOK)
    s ON (r.BOOK = s.BOOK) WHEN MATCHED THEN UPDATE SET r.TOTAL_EXPOSURE =
    s.TOTAL_EXPOSURE WHEN NOT MATCHED THEN INSERT (BOOK, TOTAL_EXPOSURE)
    VALUES (s.BOOK, s.TOTAL_EXPOSURE);`
  - `migrated/merge_load.sql`: the same MERGE lowercased, with the `ON`
    condition unparenthesised (`ON r.BOOK = s.BOOK`) and the matched update
    without the `r.` prefix (`UPDATE SET TOTAL_EXPOSURE = s.TOTAL_EXPOSURE`).
  - `onprem/multi_load.sql`: a multi-statement file — `SELECT SYSDATE FROM
    DUAL;` followed by the identical MERGE statement text from
    `onprem/merge_load.sql` (so the `sql-001` override matches it exactly,
    whitespace-normalized/case-insensitive). It is NOT listed in
    `pairs.json` (which stays at the two pairs above), so pattern learning
    is unchanged. See assertion 28.
  - `pairs.json` — exactly (paths are cwd-relative, see the note at the top
    of this section):
    `[{"approved": true, "migrated": "fixtures/exemplars/migrated/SimpleWriter.java", "onprem": "fixtures/exemplars/onprem/SimpleWriter.java"}, {"approved": true, "migrated": "fixtures/exemplars/migrated/merge_load.sql", "onprem": "fixtures/exemplars/onprem/merge_load.sql"}]`
  - `onprem/MergeWriter.java`: the fixture MERGE as a Java string literal,
    with NO trailing `;` (see assertion 32).
  - `onprem/commented_merge.sql`: a `-- a comment before the merge` line
    followed by the identical MERGE (see assertion 33).
  - `onprem/nosemi_load.sql`: on-prem MERGE with `;` (see assertion 34).
  - `migrated/nosemi_load.sql`: the Spark MERGE WITHOUT a trailing `;` —
    the file's last statement (see assertion 34).
  - `pairs-nosemi.json`: one pair (`onprem/nosemi_load.sql` →
    `migrated/nosemi_load.sql`, `approved: true`), deliberately NOT in
    `pairs.json`, so the assertion 16/23 counts are unchanged.
- `fixtures/edge-cases/EdgeCaseSql.java` — scanner edge cases (no `main()`,
  so no synthetic unit is created). Scan it with
  `scan_java.py --root fixtures/edge-cases --repo EdgeCases` (see assertion
  20). Contents:
  ```
  package com.risk.wm.jobs;

  import java.sql.CallableStatement;
  import java.sql.Connection;
  import java.sql.Statement;

  public class EdgeCaseSql {
      public void run(Connection con) throws Exception {
          try (Statement st = con.createStatement()) {
              // SELECT ... INTO var must NOT record V as a written table
              String intoSql = "SELECT X INTO v FROM T1";
              st.executeQuery(intoSql);
              // TABLE(...) reads MY_FUNC; no bogus table TABLE
              String tableSql = "SELECT * FROM TABLE(my_func)";
              st.executeQuery(tableSql);
              // prepareCall without braces must be found
              try (CallableStatement cs = con.prepareCall("CALL MYPROC()")) {
                  cs.execute();
              }
              // text block followed by a concatenated fragment merges
              String blockSql = """
                      SELECT BOOK, EXPOSURE FROM RISK_POSITIONS
                      """
                      + " WHERE AS_OF_DATE = SYSDATE";
              st.executeQuery(blockSql);
          }
      }
  }
  ```
- `fixtures/edge-cases/table_func.sql` — PL/SQL `TABLE(` edge case (see
  assertion 25). Contents:
  ```
  SELECT * FROM TABLE(my_func) t;
  SELECT a FROM t2;
  ```
  `scan_plsql.py --root fixtures/edge-cases --repo EdgeCases --exts ".sql"`
  yields `reads ['MY_FUNC', 'T2']` — no bogus `TABLE` in `reads`.
- `fixtures/edge-cases/ImportOrder.java` — import-order case (see assertion
  29): imports in non-alphabetical source order (`zebra.Z`, `alpha.A`,
  `oracle.jdbc.OracleDriver`, `middle.M`), no `main()`.
- `fixtures/edge-cases/FromTables.java` and
  `fixtures/edge-cases/from_tables.sql` — FROM-table cases (see assertion
  30), identical shapes in both scanners: `SELECT a, b FROM t1, t2`,
  `SELECT t2.c, t1.d FROM t1, t2`, and `SELECT a FROM t1 JOIN t2 ON t1.id =
  t2.id` (no `main()` in the Java file).
- `fixtures/edge-cases/CommaTables.java` and
  `fixtures/edge-cases/comma_tables.sql` — comma-only FROM case (see
  assertion 35): a single statement `SELECT a, b FROM t1, t2` (no JOIN
  statement in the file; no `main()` in the Java file).
- `fixtures/edge-cases/DerivedTables.java` and
  `fixtures/edge-cases/derived_tables.sql` — derived-table probe shapes
  (see assertion 36; no `main()` in the Java file).
- `fixtures/edge-cases/QualifiedStar.java` and
  `fixtures/edge-cases/qualified_star.sql` — qualified-star case (see
  assertion 37): `prefix.*` in the SELECT list (no `main()` in the Java
  file).
- `fixtures/edge-cases/StarRule.java` and
  `fixtures/edge-cases/star_rule.sql` — `*`-rule probe shapes (see
  assertion 38): a `*` over a WHERE-subquery table, a `*` over a derived
  table with a UNION inside it, and a `*` over a WHERE subquery with a UNION
  inside it (no `main()` in the Java file).
- `fixtures/edge-cases/UnionColumns.java` and
  `fixtures/edge-cases/union_columns.sql` — UNION/MINUS/INTERSECT-branch
  column shapes (see assertion 38): branch columns plus a top-level UNION
  `*` (no `main()` in the Java file).
- `fixtures/edge-cases/ArgsJoin.java` — `args[0]`- / `(suffix)`-joined
  ROWNUM fragments plus a benign SYSDATE method (see assertion 39; no
  `main()` in the Java file).
- `fixtures/edge-cases/StarRuleNested.java` and
  `fixtures/edge-cases/star_rule_nested.sql` — nested-WHERE / scalar-subquery
  `*` shapes (see assertion 43; no `main()` in the Java file).
- `fixtures/edge-cases/DistinctSelect.java` and
  `fixtures/edge-cases/distinct_select.sql` — `DISTINCT`/`ALL`/`UNIQUE`/
  `DISTINCTROW` select lists (see assertion 44; no `main()` in the Java
  file).
- `fixtures/edge-cases/ThreePartColumns.java` and
  `fixtures/edge-cases/three_part_columns.sql` — three-part column refs (see
  assertion 46; no `main()` in the Java file).
- `fixtures/edge-cases/WithSelectSubquery.java` and
  `fixtures/edge-cases/with_select_subquery.sql` — `WITH`-subquery in the
  SELECT list (R7-G8; no `main()` in the Java file): the CTE name never
  leaks as a column; the bare shape attributes only `T2:{A}` (the stripped
  select list is empty, as with the R6-G5 SELECT analogue), and the second
  shape gives `T1:{A}, T2:{A,B}`.
- `fixtures/edge-cases/CommentedSql.java` — commented-out Java SQL (R7-G11;
  no `main()` in the file): `tables_read == ['T_LIVE']`,
  `sql_literal_count == 1`.
- `fixtures/edge-cases/quoted_ddl.sql` — quoted `CREATE TABLE` (R7-G12):
  `ddl_columns == {'RISK_POSITIONS': ['A', 'B'], 'RISK_CTRL': ['P', 'Q'],
  'SCH.RISK_AUDIT_Q': ['X']}`.
- `fixtures/scripts/jar_main.sh` — `-jar`-terminal gate case (R7-G9):
  `java -jar app.jar com.foo.Bar` reports no main, while
  `java -cp lib.jar com.foo.Real` reports `com.foo.Real`;
  `jars == ['app.jar', 'lib.jar']`.
- `fixtures/autosys/jobs-wrapped-only.csv` — WRAPPED_JOB-only jobs (see
  assertion 42).
- `fixtures/exemplars/pairs-semi.json` plus `onprem/semi_load.sql` and
  `migrated/semi_load.sql` — reverse-terminator pair (see assertion 45):
  the on-prem MERGE has NO trailing `;`, the migrated MERGE has one.

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
4. `build_graph.py` with `jobs: []` creates 3 synthetic units
   (`main:LegacyJob`, `main:com.risk.wm.jobs.NightlyRiskJob`,
   `main:com.risk.wm.jobs.RiskReportJob`) and a `likely_depends_on` edge from
   `main:com.risk.wm.jobs.RiskReportJob` to
   `main:com.risk.wm.jobs.NightlyRiskJob` via `RISK_RESULTS` — and the
   nightly unit's `RISK_RESULTS` is kind `read` with `transitive_write
   write_via_proc` (per the 4.5 precedence table), NOT kind
   `write_via_proc`.
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
9. Column extraction (compare as SETS where stated — `table_columns` values
   are sorted, `ddl_columns` preserve DDL order; see 4.1/4.2):
   `DbUtil` reports `table_columns.RISK_POSITIONS.write =
   [AS_OF_DATE, BOOK, EXPOSURE, POSITION_ID]` (sorted order); the DDL file
   reports `ddl_columns.RISK_POSITIONS` with the same four columns in DDL
   order (`POSITION_ID, BOOK, EXPOSURE, AS_OF_DATE`); `NightlyRiskJob`
   reports `RISK_RESULTS.read = [TOTAL_EXPOSURE]` and `RISK_POSITIONS.read`
   includes `BOOK`, `EXPOSURE` and `*` (the `*` comes from the outer `*`
   over a single derived table — see 4.1);
   the nightly unit's `RISK_POSITIONS.columns_read` also includes `P_ASOF`
   (the procedure parameter in the package SELECT list, merged in from the
   defining PL/SQL record per 4.5); and the nightly unit's
   `RISK_RESULTS.columns_written = [AS_OF_DATE, BOOK, TOTAL_EXPOSURE]`
   (same merge).
10. `build_process_docs.py` writes one HTML per unit plus `index.html`; the
    nightly doc contains the DRAFT description, the dependency chain, an
    Inputs table listing RISK_POSITIONS, and an inline `<svg>` diagram.
    `build_table_catalog.py` writes `tables.md` with a `RISK_POSITIONS`
    section listing the DDL columns and the nightly job under both readers
    and writers.
11. `translate_sql.py`: `NVL(x, 0)` → `COALESCE(x, 0)`, `SYSDATE` →
    `current_timestamp()`, `TO_DATE('...','YYYY-MM-DD')` →
    `to_date('...','yyyy-MM-dd')` with confidence `medium`,
    `DECODE(...)` → `CASE ... END`, while a
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
14. Single-process mode, from the kit root:
    ```
    python3 scripts/run_process.py --process NIGHTLY_RISK_JOB \
      --repos Risk_DB=fixtures/Risk_DB,Risk_DL=fixtures/Risk_DL,CoreWM=fixtures/CoreWM,scripts=fixtures/scripts \
      --jobs fixtures/autosys/jobs.csv --ignore fixtures/ignore.json
    ```
    produces exactly one unit in `units.json`, one HTML doc plus
    `index.html`, a `tables.md` with only `RISK_POSITIONS`, `RISK_RESULTS`
    and `RISK_AUDIT` (3 tables with `--ignore`; a 4th, `V$SESSION`, appears
    without `--ignore`), a `batches.json` with one batch containing only
    `NIGHTLY_RISK_JOB`, and a migration tree with exactly the process's
    files — `NightlyRiskJob.java`, `DbUtil.java`, `risk_tables.sql`,
    `risk_calc_pkg.pkb`, `post_load.sql`, `run_nightly.sh` (6 mirrored source files, plus `translation-bundles/`
    and `translation-report.md`) — with
    `NVL`/`SYSDATE` translated in place and the `ROWNUM` fragment flagged;
    the unit lists `RISK_AUDIT` as `write_via_script` (the wrapper script's
    `sqlplus` file belongs to the process even though the job command calls
    Java directly). The `scripts=fixtures/scripts` fourth repo is required:
    with only the three §1 repos the tree has 4 files and no `RISK_AUDIT`,
    because the wrapper is never scanned. The four §5 report files are also
    written to `output/` (see assertion 19).
15. Without `--only`, the same run without `--ignore` (the §5 pipeline end to end — scan +
    `build_graph.py` + `classify.py` + `build_process_docs.py` +
    `build_table_catalog.py` + `prepare_migration_tree.py` +
    `compile_reports.py`, none with `--only`, from the kit root with the
    same four repos and `fixtures/autosys/jobs.csv`) still produces all 6
    units, 6 docs + `index.html`, and the full 9-file tree (9 mirrored
    source files, plus bundles and report — the 8 of round 6 plus the
    `jar_main.sh` fixture added in round 7) — filtering is
    opt-in, never silent. With only the three §1 repos (no `scripts` repo)
    the full tree has 7 files: the wrapper `run_nightly.sh` is missing.
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
19. `compile_reports.py` (see 4.18), run on the fixture output — e.g. the
    standalone command from §5 step 4, or the `run_process.py` output of
    assertion 14 — writes all four files into the `--outdir`:
    `process-catalog.csv` (header exactly
    `unit_id,preclassification,verdict,confidence,summary,key_db_objects,
    java_file_count,repos,linkage`), `classification-report.md` (label and
    verdict counts, the per-class migration-planning meanings, the
    NEEDS_REVIEW / UNKNOWN / no-verdict-file lists, aggregated human-review
    flags, and the Risk_DL shared-library note),
    `oracle-dialect-census.csv` (header exactly `feature,occurrences`),
    and `db-object-catalog.csv` (header exactly
    `object,kind,defined_in,used_by_units`). `run_process.py` writes them to
    `<outdir>/output` as its final step.
20. Edge-case fixture scan: `scan_java.py --root fixtures/edge-cases
    --repo EdgeCases` yields `tables_read ['MY_FUNC', 'RISK_POSITIONS',
    'T1']`, `tables_written []`, `procs_called ['CALLMYPROC']`,
    `sql_literal_count 4`, `has_main` false; `sql_sample[3]` is
    `SELECT BOOK, EXPOSURE FROM RISK_POSITIONS ? WHERE AS_OF_DATE = SYSDATE`
    (the `?` is the placeholder the merge leaves where the text block and
    the concatenated fragment join). `table_columns` also records the known
    depth-blind artifact `TABLE: {read: ['*']}` (see 4.1).
21. SVG upstream/downstream: the nightly doc's SVG Upstream layer contains
    `FEED_LANDED` and `MARKET_DATA_READY` and does NOT contain the synthetic
    unit `main:com.risk.wm.jobs.RiskReportJob` — it is in the Downstream
    layer, rendered truncated as `main:com.risk.wm.jobs.Ris…` (SVG labels
    are truncated with an ellipsis; see 4.11). Upstream comes from the
    unit's `depends_on` ∪ `likely_depends_on`, downstream from its
    `likely_required_by` (see 4.11). The nightly doc's SVG Oracle layer
    shows the abbreviated kinds with a legend: `RISK_AUDIT (w_vs)` and
    `RISK_CALC_PKG.CALC… (call)`, plus the line `Oracle kinds: w=write,
    r=read, w_vs=write_via_script, w_vp=write_via_proc, r_vs=read_via_script,
    r_vp=read_via_proc, call=call`.
22. Hint removal: `translate()` on `SELECT /*+ FULL(t) */ * FROM t`
    returns `applied` containing `optimizer hint removed` and translated
    text with no `/*+`; the `post_load.sql` bundle (assertion 14's tree)
    shows `applied: ['SYSDATE->current_timestamp()',
    'optimizer hint removed']` for its SELECT; a hint inside a string
    literal survives unchanged. A `SYSTIMESTAMP`-only rewrite records
    `SYSTIMESTAMP->current_timestamp()` in `applied` (a `SYSDATE`-only
    rewrite records `SYSDATE->current_timestamp()`; both firing records
    both).
23. `--base` re-approval: run `learn_patterns.py --pairs
    fixtures/exemplars/pairs.json --migrated-dir fixtures/exemplars/migrated
    --out p1.json` once WITHOUT `--approve` (all three entries `proposed`),
    then run it again with `--base p1.json --approve auto --out p2.json` on
    the same pairs → all three entries are `approved` in `p2.json`, counts
    still 1 (the persisted `unambiguous`/`vetted` flags are what the
    re-evaluation reads — see 4.16).
24. Flag-only PL/SQL statements reach the tree: in the full tree
    (assertion 15), `translation-report.md` lists 2 flagged files — the
    Java file with the `ROWNUM` fragment and
    `Risk_DB/pkg/risk_calc_pkg.pkb` — and the `.pkb` bundle lists the
    `EXECUTE IMMEDIATE` and `DBMS_` flags (its header reports 2 flag(s)
    without `--ignore`; 1 flag(s) with `--ignore`). The `.pkb` bundle now
    lists 6 statements (was 2): the 2 flagged ones plus 4 no-ops recorded
    with `applied: []`, `flags: []`, `confidence: high` (see 4.14).
25. `TABLE(` in PL/SQL: `scan_plsql.py --root fixtures/edge-cases --repo
    EdgeCases --exts ".sql"` yields `reads ['MY_FUNC', 'T2']` for
    `table_func.sql` — no `TABLE` in `reads` (`_read_tables` skips `TABLE`;
    `_TABLE_FUNC_RE` supplies `MY_FUNC`, see 4.1/4.2).
26. `--only` without `--units` / `--graph` / `--catalog` mirrors nothing
    and does NOT crash: `prepare_migration_tree.py --repos
    Risk_DB=fixtures/Risk_DB --only NIGHTLY_RISK_JOB --outdir <tmp>`
    exits 0, creates the outdir, writes an empty tree (no mirrored files)
    plus `translation-report.md` reporting `Files mirrored: 0` (see 4.14).
27. SVG in `--only` mode: in the single-process run (assertion 14), the
    nightly doc's `<svg>` block contains no Downstream layer — the unit's
    `likely_required_by` is `[]` and the text section says None — because
    the SVG layers come from the unit's pruned `depends_on` ∪
    `likely_depends_on` and `likely_required_by` lists, not from graph
    edges (see 4.11).
28. Multi-statement PL/SQL override (R4-B1): with the approved
    `patterns.json` from assertion 16, `prepare_migration_tree.py --repos
    ex=fixtures/exemplars/onprem --patterns patterns.json` mirrors
    `multi_load.sql` with the Spark MERGE in the file body (not only in the
    bundle): the mirrored body contains `ON r.BOOK = s.BOOK` and `UPDATE SET
    TOTAL_EXPOSURE = s.TOTAL_EXPOSURE` and no `ON (r.BOOK = s.BOOK)` or
    `UPDATE SET r.TOTAL_EXPOSURE` (the original Oracle MERGE text), and the
    first statement is translated to `SELECT current_timestamp();`. Its
    bundle records `patterns_applied: ["sql-001"]` with both statements
    `confidence: high` and no flags.
29. Import order under `--ignore`: `scan_java.py --root fixtures/edge-cases
    --repo EdgeCases --ignore fixtures/ignore.json` reports the
    `ImportOrder.java` record with `imports == ['zebra.Z', 'alpha.A',
    'middle.M']` (source order, not sorted) and `ignored_imports ==
    ['oracle.jdbc.OracleDriver']`. (Without `--ignore` the same record
    reports all four imports in source order.)
30. FROM-table scan (bare `JOIN` and comma joins): `scan_java.py --root
    fixtures/edge-cases --repo EdgeCases` and `scan_plsql.py --root
    fixtures/edge-cases --repo EdgeCases --exts ".sql"` report, for the
    `FromTables.java` / `from_tables.sql` records respectively, the same
    values in both scanners (field names differ — `tables_read` in Java,
    `reads` in PL/SQL): `tables_read` (Java) / `reads` (PL/SQL) ==
    ['T1', 'T2'], `table_columns == {'T1':
    {'read': ['D'], 'write': []}, 'T2': {'read': ['C'], 'write': []}}`,
    `columns_unattributed == ['A', 'B']` — the bare columns of `SELECT a, b
    FROM t1, t2` and of `SELECT a FROM t1 JOIN t2 ON ...` are multi-table
    bare columns, not attributed to `T1`, while `SELECT t2.c, t1.d FROM t1,
    t2` attributes `T2.C` and `T1.D` to their tables.
31. Determinism: run `build_graph.py` twice on the same scanner outputs
    (e.g. with different `PYTHONHASHSEED` values) and `diff` the two
    `units.json` files — they are byte-identical. In particular the nightly
    unit's `java_files` is sorted by path
    (`fixtures/CoreWM/jobs/com/risk/wm/jobs/NightlyRiskJob.java` before
    `fixtures/Risk_DL/common/com/risk/dl/DbUtil.java`) on every run.
32. Java override match (R5-G1): with the approved `patterns.json` from
    assertion 16, `prepare_migration_tree.py --repos
    ex=fixtures/exemplars/onprem --patterns patterns.json` translates the
    `MergeWriter.java` literal (the fixture MERGE as a Java string literal,
    with NO trailing `;`) with `applied: ["pattern:sql-001"]`,
    `confidence: high`, no flags; the mirrored literal is the stored
    override `to` as-is.
33. Commented override (R5-G2): the same tree run mirrors
    `commented_merge.sql` — a `-- a comment before the merge` line followed
    by the identical MERGE — with the comment preserved at the head of the
    mirrored body, the Spark MERGE below it, and the bundle recording
    `applied: ["pattern:sql-001"]` with no flags.
34. Terminator preserved (R5-G3): learning from
    `fixtures/exemplars/pairs-nosemi.json` (`--approve auto`) yields an
    approved `sql-001` whose `to` has NO trailing `;` (the migrated
    statement is the file's last statement and lacks one); running the tree
    on `fixtures/exemplars/onprem` with that patterns file still mirrors
    `multi_load.sql` (and `nosemi_load.sql`) with the MERGE ending in `;`,
    and the bundle records `applied: ["pattern:sql-001"]`.
35. `CommaTables.java` / `comma_tables.sql` (comma-only, no JOIN statement
    in the file): `tables_read` (Java) / `reads` (PL/SQL) `== ['T1', 'T2']`,
    `table_columns == {}`, `columns_unattributed == ['A', 'B']`.
36. `DerivedTables.java` / `derived_tables.sql`: `tables_read` (Java) /
    `reads` (PL/SQL) `== ['T1', 'T2']`, `table_columns == {'T1': {'read':
    ['A', 'B'], 'write': []}, 'T2': {'read': ['A'], 'write': []}}`,
    `columns_unattributed == []`.
37. `QualifiedStar.java` / `qualified_star.sql`: `tables_read` (Java) /
    `reads` (PL/SQL) `== ['T1', 'T2']`, `table_columns == {'T1': {'read':
    ['*'], 'write': []}, 'T2': {'read': ['C'], 'write': []}}`,
    `columns_unattributed == []`.
38. `*`-rule shapes, both scanners, on `StarRule.java` / `star_rule.sql`
    and `UnionColumns.java` / `union_columns.sql`:
    (a) `SELECT * FROM t1 WHERE x IN (SELECT y FROM t2)` records `*` under
    `T1` and `Y` under `T2` — the WHERE subquery's table does not count
    toward the `*` candidate set; the same holds for
    `SELECT * FROM t1 WHERE x IN (SELECT y FROM t2 UNION SELECT y FROM t3)`
    (`*` under `T1`, `Y` under `T2` and `T3`).
    (b) `SELECT * FROM (SELECT a FROM t1 UNION SELECT a FROM t2) x` drops
    `*` (its derived table names two tables) and records `A` under both
    `T1` and `T2`.
    Whole-record values, identical in both scanners (field names differ —
    `tables_read` in Java, `reads` in PL/SQL): `StarRule` →
    `tables_read` / `reads == ['T1', 'T2', 'T3']`, `table_columns == {'T1':
    {'read': ['*', 'A'], 'write': []}, 'T2': {'read': ['A', 'Y'],
    'write': []}, 'T3': {'read': ['Y'], 'write': []}}`,
    `columns_unattributed == []`; `UnionColumns` → `tables_read` / `reads
    == ['T1', 'T2']`, `table_columns == {'T1': {'read': ['A'], 'write': []},
    'T2': {'read': ['B'], 'write': []}}`, `columns_unattributed == []` —
    UNION/MINUS/INTERSECT branch columns are attributed, and the top-level
    UNION `*` (`SELECT * FROM t1 UNION SELECT * FROM t2`) is dropped as
    multi-table.
39. Tree on `fixtures/edge-cases/ArgsJoin.java`: the scanner reports 3
    literals, and each ROWNUM method merges to one literal
    `SELECT a FROM t  ?  WHERE ROWNUM <= 5` with `oracle_isms {ROWNUM: 1}`
    (record total `ROWNUM: 2`). The tree produces a bundle item for each
    ` WHERE ROWNUM <= 5` fragment with the ROWNUM flag (`ROWNUM: rewrite
    with ROW_NUMBER() OVER (ORDER BY ...) — needs ORDER BY context`),
    `confidence: needs-review`, `chain_sql`
    `SELECT a FROM t  ?  WHERE ROWNUM <= 5`, and a
    `/* TODO-SPARK: ROWNUM … */` marker after the fragment in the mirrored
    file (one per ROWNUM method — `args[0]`-joined and `(suffix)`-joined).
    The benign SYSDATE method translates with no flags
    (`SELECT SYSDATE FROM DUAL ` → `SELECT current_timestamp() `, `applied`
    `['FROM DUAL removed', 'SYSDATE->current_timestamp()']`; its trailing
    ` FROM DUAL` fragment becomes `""`).
40. Learn from the commented pair (`fixtures/exemplars/onprem/
    commented_merge.sql` → `fixtures/exemplars/migrated/merge_load.sql`,
    one-pair pairs file with the entry carrying `"approved": true`,
    `--approve auto`): the learned `sql-001` is
    approved and its `from` starts `MERGE INTO RISK_RESULTS r …` — no
    leading comment. Applying that patterns file back with
    `prepare_migration_tree.py --repos ex=fixtures/exemplars/onprem`
    mirrors `commented_merge.sql` with the comment preserved at the head of
    the mirrored body and the Spark MERGE below it, and the bundle records
    `applied: ["pattern:sql-001"]` (top-level and per-statement) with
    `patterns_applied: ["sql-001"]` and no flags.
41. `grep "Mechanical rewrites split across concatenated fragments"` in
    `translation-report.md` finds the line in both the full-run report and
    the single-process run's report (assertion 14's
    `migration-tree/translation-report.md`).

Runnable guard (R7-G2): with `scripts/` on `sys.path`,
`prepare_migration_tree.BETWEEN_RE.pattern == scan_java.BETWEEN_RE.pattern`
holds — `BETWEEN_RE` is one shared module-level constant in `scan_java.py`,
imported by `prepare_migration_tree.py`, never forked (see 4.1/4.14).

42. Script-only synthetic coverage (R7-B2): a jobs file built from
    `fixtures/autosys/jobs-wrapped-only.csv` (same Autosys header as
    `jobs.csv`, a single row for `WRAPPED_JOB`, whose command is the
    wrapper `.sh`, no direct Java) yields exactly 3 units — `WRAPPED_JOB`,
    synthetic `main:LegacyJob`, and synthetic
    `main:com.risk.wm.jobs.RiskReportJob` — and NO synthetic
    `main:com.risk.wm.jobs.NightlyRiskJob` (the nightly class is reached via
    the wrapper script, so it counts as referenced; see 4.5).
43. Output-tables `*` rule (R7-G1), both scanners, on
    `fixtures/edge-cases/StarRuleNested.java` /
    `fixtures/edge-cases/star_rule_nested.sql` (two statements, one record
    per file — values below are the file-level record, the union of both
    shapes; see 4.1): `tables_read` / `reads == ['T1', 'T2']`,
    `table_columns == {'T1': {'read': ['*', 'A'], 'write': []}, 'T2':
    {'read': ['C', 'Y'], 'write': []}}`, `columns_unattributed == []`.
    The first statement (`SELECT * FROM (SELECT a FROM t1 WHERE x IN
    (SELECT y FROM t2)) x`) keeps `*` under `T1` (the WHERE subquery nested
    inside the derived table does not count); the second (`SELECT * FROM
    (SELECT (SELECT MAX(c) FROM t2) FROM t1) x`) drops `*` (its output
    column comes from T2 via the SELECT-list scalar subquery), so `T2`
    contributes both `C` (scalar) and `Y` (nested WHERE) to the record.
44. `DISTINCT` / `ALL` select lists (R7-G4), both scanners, on
    `fixtures/edge-cases/DistinctSelect.java` /
    `fixtures/edge-cases/distinct_select.sql` (five statements — `SELECT
    DISTINCT a, b`, `SELECT ALL a`, `SELECT UNIQUE a`, `SELECT DISTINCTROW
    a`, `SELECT DISTINCT t1.a`, all from t1; values below are the
    file-level record): `tables_read` / `reads == ['T1']`,
    `table_columns == {'T1': {'read': ['A', 'B'], 'write': []}}`,
    `columns_unattributed == []` (no keyword recorded as a column, no
    first column eaten).
45. Terminator preserved in both directions (R7-G3 option b): learn
    `fixtures/exemplars/pairs-semi.json` (one pair,
    `onprem/semi_load.sql` → `migrated/semi_load.sql`, `approved: true` —
    the on-prem MERGE has NO trailing `;`, the migrated MERGE has one);
    the tree on `onprem/semi_load.sql` mirrors with NO `;` gained in the
    mirrored PL/SQL body (the trailing `;` is stripped from the
    replacement), and the bundle records `applied: ["pattern:sql-001"]`.
46. Three-part column refs (R7-G7), both scanners, on
    `fixtures/edge-cases/ThreePartColumns.java` /
    `fixtures/edge-cases/three_part_columns.sql` (three statements; values
    below are the file-level record): `tables_read` / `reads ==
    ['SCHEMA1.T1', 'T1']`, `table_columns == {'SCHEMA1.T1': {'read':
    ['A', 'B'], 'write': []}}`, `columns_unattributed == ['FOO.BAR.A']`.
    `SELECT schema1.t1.a, schema1.t1.b FROM schema1.t1` resolves through
    the full name; `SELECT s1.t1.a FROM schema1.t1 s1` resolves through
    the short name; `SELECT foo.bar.a FROM t1` stays unattributed as
    `FOO.BAR.A` (genuinely unknown prefix).

## 7. Definition of done

- Every file exists at the specified path; all scripts are stdlib-only and
  executable via the documented CLI flags.
- All forty-six self-test assertions pass; paste the verification output.
- The review prompt, config, and README are complete and consistent with the
  scripts (flag names, JSON field names, and the checkpoint sanitize rule
  match exactly).
- No LLM is required to run the scan phase; the review phase never re-does a
  checkpointed unit.

Build it now.

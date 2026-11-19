# 4-eye review: migration-kit build prompt as a standalone rebuild spec

- **Reviewed:** `migration-kit-build-prompt.md` (740 lines) vs the kit at `~/workspace/migration-kit/` (14 scripts, `config.yaml`, `README.md`, `prompts/classify_process.md`, fixtures)
- **Date:** 2026-11-16
- **Method:** independent review — the prompt was read in full; every script was read and compared against its spec; the full pipeline was executed in `/tmp` against the fixtures and each of the 18 self-test assertions was checked against real output. Three parallel sub-reviews covered scanners (§§4.1–4.4), graph/docs (§§4.5–4.11), and translator/tree/patterns (§§4.12–4.17, §5). Findings below were verified against the actual files, not taken from any summary.
- **Verdict: NEEDS FIXES FIRST.** The prompt is close — most specs are accurate and 14 of the 18 assertions pass as written — but it cannot be used as a standalone rebuild spec as-is: three assertions fail as written (4, 14, 15), §5's compile step has no implementation at all, and a faithful rebuild would diverge from the kit in at least a dozen places, several of them in the newest features (patterns, `--only`, wrapper scripts).

## Severity key

- **Blocker** — a rebuild following the prompt would produce behavior different from the kit, or the prompt's own self-test cannot pass as written.
- **Gap** — a spec, behavior, or fixture the prompt needs but does not have (or vice versa); a rebuild would have to guess.
- **Nit** — wording, numbering, or precision issue that does not change behavior but can mislead.

---

## Blockers

### B1 — §5: the compile step has no implementation (§5, step 4)
**What is wrong:** §5 step 4 requires four outputs with exact schemas — `process-catalog.csv`, `classification-report.md`, `oracle-dialect-census.csv`, `db-object-catalog.csv`. No script in the kit produces any of them (grep over all 14 scripts finds zero references to those filenames). `build_graph.py` writes only `db-object-catalog.json` (`[{object, kind, defined_in[]}]`, without the `used_by_units` field the CSV requires). §3's layout lists no compile script either, and the README's Step 4 only says "ask Muse to compile the reports".
**Fix:** either add a §4.18 spec for a `compile_reports.py` (inputs `state/graph/units.json` + `state/units/` checkpoints, exact CSV/MD schemas from §5) and list it in §3, or rewrite §5 step 4 as an explicit agent/manual step stating who does it and from which inputs. Do not leave a specified output with no producer.

### B2 — Assertion 14 fails as specified, and is internally contradictory (§6, assertion 14; §4.11)
**What is wrong:** (a) The assertion says `run_process.py --process NIGHTLY_RISK_JOB` "on the fixtures" produces a 6-file tree including `post_load.sql` and `run_nightly.sh`, and the unit listing `RISK_AUDIT` as `write_via_script`. It never states the `--repos` argument. With the three repos named in §1 (`Risk_DB`, `Risk_DL`, `CoreWM`), the run produces only **4** tree files and no `RISK_AUDIT`: `fixtures/scripts/run_nightly.sh` lives outside all three repos, `run_process.py` only runs `scan_scripts.py --root <repo>` per repo, and `collect_process_files` builds its basename index only by walking the repos — so the wrapper is never scanned and the reverse wrapper association never fires (the run warns `cannot locate /opt/risk/bin/run_nightly.sh`). It only passes if `scripts=fixtures/scripts` is passed as a fourth repo, which the prompt never says. (b) The same assertion requires `tables.md` to contain "only `RISK_POSITIONS` and `RISK_RESULTS`" **and** the unit to list `RISK_AUDIT` as `write_via_script` — but `build_table_catalog.py` includes `write_via_script` in its write kinds, so a unit with `RISK_AUDIT write_via_script` produces a third `RISK_AUDIT` section. Verified: `--only NIGHTLY_RISK_JOB` with `--ignore` yields 3 tables (`RISK_POSITIONS`, `RISK_RESULTS`, `RISK_AUDIT`); without `--ignore`, 4 (plus `V$SESSION`). Both halves cannot hold.
**Fix:** give the exact `run_process.py` command in the assertion, including `--repos … ,scripts=fixtures/scripts` (or move `run_nightly.sh` into one of the three repos and update §3/§6), and correct the tables list to `RISK_POSITIONS`, `RISK_RESULTS`, `RISK_AUDIT` (plus `V$SESSION` when run without `--ignore`).

### B3 — Assertion 15's "8-file tree" is unachievable the same way (§6, assertion 15)
**What is wrong:** "the full 8-file tree" holds only if `fixtures/scripts` is passed as a repo (8 files); with the three §1 repos the full tree has 7 files. Same root cause as B2.
**Fix:** same as B2 — pin the command and the count to the same repo set.

### B4 — Assertion 4: "the two `main()` classes" is wrong (§6, assertion 4)
**What is wrong:** the fixtures have **three** `main()` classes (`NightlyRiskJob`, `RiskReportJob`, `LegacyJob`); with `jobs: []` the code creates 3 synthetic units, not 2. The `likely_depends_on` edge itself exists (`main:RiskReportJob → main:NightlyRiskJob` via `RISK_RESULTS`, nightly `write_via_proc` ✓), but only in the `jobs: []` run — with the Autosys jobs loaded, the edge's source is the *synthetic* `main:com.risk.wm.jobs.RiskReportJob`, while the scheduler `RISK_REPORT_JOB` has linkage `missing` and empty `db_objects`. "The report job" is ambiguous between the two.
**Fix:** rewrite as: "with `jobs: []` the code creates 3 synthetic units (`main:LegacyJob`, `main:com.risk.wm.jobs.NightlyRiskJob`, `main:com.risk.wm.jobs.RiskReportJob`) and a `likely_depends_on` edge from `main:com.risk.wm.jobs.RiskReportJob` to `main:com.risk.wm.jobs.NightlyRiskJob` via `RISK_RESULTS`."

### B5 — §4.5: via-proc columns are never merged (`scripts/build_graph.py`, unit construction)
**What is wrong:** the prompt says "Columns are merged from the Java/PLSQL `table_columns` of the unit's files (direct + via-script + via-proc)". The code's transitive loop only sets `tables[t]` kinds from `db_access` (read/write sets) and never touches `table_cols`; columns are merged only from Java files and script SQL files. Verified: the `risk_calc_pkg.pkb` scan records `RISK_RESULTS.write = [AS_OF_DATE, BOOK, TOTAL_EXPOSURE]`, but the nightly unit's `RISK_RESULTS` (`write_via_proc`) has `columns_written: []`. Every via-proc table's column lists in docs and catalog are empty.
**Fix:** decide the truth and align one side — either fix the code to merge `table_columns` from the defining PL/SQL records for via-proc tables, or weaken the prompt to "columns are merged for direct and via-script tables only".

### B6 — §4.10: SVG upstream/downstream layers are inverted (`scripts/build_process_docs.py`, `build_svg`)
**What is wrong:** the prompt layers the diagram "upstream jobs | this job | scripts/jars | Java | Oracle | downstream jobs". The code builds `up` from `in_e` (edges pointing *at* this job) and `down` from `out_e` (edges *leaving* it) — but `likely_depends_on` / `depends_on` edges point dependent → dependency (`job:B → job:A` when B depends on A), so upstream is the out-edges and downstream the in-edges: exactly backwards. Verified in live output: the nightly doc's SVG "Upstream" layer lists `RISK_REPORT_JOB` and `main:com.risk.wm.jobs.RiskReportJob` — both *consumers* of nightly's output — while the doc's own "Upstream dependencies" text section correctly lists `FEED_LANDED` / `MARKET_DATA_READY` and "Downstream dependents" lists `main:com.risk.wm.jobs.RiskReportJob`. The doc contradicts its own diagram.
**Fix:** swap the two computations in `build_svg` (upstream from `out_e` kinds `depends_on`/`likely_depends_on`, downstream from `in_e` kind `likely_depends_on`), and add an assertion that the nightly SVG's Upstream layer contains `FEED_LANDED` and not `RISK_REPORT_JOB`.

### B7 — §4.16: `--approve auto` "≥3 pairs" rule is missing, and same-run repeats are not merged (`scripts/learn_patterns.py`, `merge_list`)
**What is wrong:** the prompt says auto-approve is "exactly one removed + one added import in the pair, **or the same pairing in ≥3 pairs**". The code approves only on `e.get('unambiguous')` or `e.get('vetted')`; there is no count/≥3 check anywhere, and on a merge hit it only unions evidence without re-evaluating status. Worse, `merge_list` builds its dedup index only from the base list, never from entries appended during the same call, so repeats *within* one run are not merged. Verified with 3 identical ambiguous pairs (2 removed × 2 added imports): output was **12** library entries (4 cross-products × 3 pairs), all `proposed`, `count: 1`, evidence listed separately — instead of 4 entries with `count: 3`.
**Fix:** in `merge_list`, rebuild/extend the dedup index as entries are appended, and after merging re-evaluate status (promote to `approved` when `count >= 3` and `--approve auto`); or delete the "≥3 pairs" clause from the prompt.

### B8 — §4.12: `/*+ */` hint removal is dead code (`scripts/translate_sql.py`, `translate()`)
**What is wrong:** the prompt says "`/*+ */` hints removed". The code splits SQL into segments with `re.split(r"('(?:''|[^'])*'|/\*.*?\*/|--[^\n]*)", …)` and runs the hint-removal `re.sub` only on the even (non-string/comment) segments — but a `/*+ … */` hint matches the comment alternative, so it always lands in an odd segment and is preserved verbatim. Verified: `SELECT /*+ FULL(t) */ * FROM t WHERE ROWNUM<5` → unchanged, `applied: []`. The rule can never fire.
**Fix:** apply hint removal before the segment split (or on the comment segments), and add a self-test case for it; or remove the rule from the prompt.

### B9 — §4.12: `TO_DATE` returns `high`, not `medium` (`scripts/translate_sql.py`, confidence block)
**What is wrong:** the prompt says "Confidence: `needs-review` if any flags, `medium` if DECODE/format mapping applied, else `high`." The code's test is `'DECODE->CASE' in applied or any('format' in a for a in applied)`; `TO_DATE`'s applied name is `'TO_DATE->to_date'` (no substring `format`), so only `TO_CHAR->date_format` triggers medium. Verified: `TO_DATE('2020-01-01','YYYY-MM-DD')` → `confidence: high`. A rebuild following the prompt would return `medium` for `TO_DATE`.
**Fix:** change the code's test to include `TO_DATE->to_date` (e.g. check for `'->to_date'` or `'->date_format'`), or narrow the prompt to "`medium` if DECODE or TO_CHAR format mapping applied".

### B10 — §4.1: `INTO` guard missing (`scripts/scan_java.py`)
**What is wrong:** the prompt says write extraction uses "`\bINTO\s+(name)` **only when preceded by INSERT** (PL/SQL `SELECT ... INTO var` must NOT count)". The code's `INTO` pattern has no preceding-INSERT check. Verified by probe: `"SELECT x INTO v FROM T1"` produced `tables_written=['V']` — the INTO-variable recorded as a written table. The code *does* strip `SELECT...INTO` in column extraction but not in table extraction — internally inconsistent. Fixtures contain no `SELECT...INTO`, so the self-test cannot catch it.
**Fix:** add the guard in the code (require the `INTO` to follow `INSERT` in the same statement) and add a fixture case; or correct the prompt.

### B11 — §4.1: wrong `TABLE(` pattern, inconsistent with `scan_plsql.py` (`scripts/scan_java.py`)
**What is wrong:** the prompt says read extraction includes "`\bTABLE\s*\(\s*(name)`". The code uses `r'\bTABLE\s+([A-Za-z_]...'` — whitespace + name, no parenthesis. Verified by probe: `"SELECT * FROM TABLE(my_func)"` produced `tables_read=[..., 'TABLE']` (bogus table `TABLE`, captured by the FROM pattern) and missed `my_func` entirely. `scan_plsql.py` has the correct paren form, so the two scanners disagree with each other as well as with the spec.
**Fix:** change the Java pattern to the paren form and add a fixture case.

### B12 — §4.1: `prepareCall` extraction is dead code (`scripts/scan_java.py`)
**What is wrong:** the prompt says proc calls come from "`{call ...}` inside SQL literals, plus `prepareCall("...")` in code". The code strips all string literals to `""` first and only then runs `PREPCALL.finditer(code)` — but `PREPCALL` requires 1+ characters inside the quotes, so it can never match. Only the `{call ...}` path works; the fixture passes via that path, masking the dead code. A rebuild implementing the spec would emit extra/different `procs_called` entries for `prepareCall` forms without braces (e.g. `prepareCall("CALL MYPROC()")`).
**Fix:** run the `prepareCall` extraction on the text *before* literal stripping (or on the extracted literals), and add a fixture case.

### B13 — §4.1: text-block/string span positions are mixed, breaking literal merging (`scripts/scan_java.py`)
**What is wrong:** the prompt says "Extract string literals: `"..."` (with escapes) and `"""..."""` text blocks. Merge literals concatenated with `+`." The code collects text-block spans in original-text coordinates, then replaces text blocks with `""` and collects `"..."` spans in the *shortened* coordinates, sorts them together, and tests the gap on the original text. After any text block, every later string span is shifted early, so the gap slice is wrong and the `between` test never matches. Verified by probe: a text block followed by `+ " WHERE X = 1"` did **not** merge — `sql_literal_count=2`, the WHERE fragment silently dropped. A rebuild from the prompt (one coordinate space) would merge it.
**Fix:** collect all literal spans in one coordinate space (or re-run the string scan on the text with text blocks replaced *and* adjust offsets); add a fixture with a text block followed by a concatenated fragment.

### B14 — §4.3: Control-M records omit 5 of the 13 promised output fields (`scripts/scan_jobs.py`, `parse_xml`/`parse_csv`)
**What is wrong:** the prompt promises per job "`job_name, job_id, description, command, host, schedule, depends_on[], parent_jobs[], task_name, parameters, main_class, scripts[], jars[]`". Only `parse_autosys` emits all 13 (verified). `parse_xml` and `parse_csv` emit only 8 (`job_name, command, host, schedule, depends_on, main_class, scripts, jars`) — no `job_id`, `description`, `parent_jobs`, `task_name`, `parameters`. Separately, the prompt says the XML reader reads attributes "`JOBNAME|NAME`, `CMDLINE|COMMAND|CMD`, `NODEID|HOST`, `DESCRIPTION`" — the code never reads `DESCRIPTION` at all.
**Fix:** make the Control-M parsers emit all 13 fields (empty/default where the format has none) and read `DESCRIPTION`; or narrow the prompt's output contract to "Autosys emits all 13; Control-M emits the 8 it has".

### B15 — §4.3: XML job-element match is narrower than specified (`scripts/scan_jobs.py`, `parse_xml`)
**What is wrong:** the prompt says "find elements whose tag ends in `JOB` (case-insensitive)". The code does equality after namespace-strip (`tag != 'JOB'`), so `<MYJOB>` / `<CONTROLMJOB>` would match the spec but is missed by the code.
**Fix:** change the code to an endswith test, or narrow the prompt to "elements whose tag is exactly `JOB`".

---

## Gaps

### G1 — §6 fixture list omits `ignore.json`, the exemplars, and two fixture details assertions depend on (§6)
**What is wrong:** §6 "Create small synthetic fixtures exercising every feature" never mentions `fixtures/ignore.json`, `fixtures/exemplars/` (SimpleWriter pair, MERGE pair, `pairs.json`), the `V$SESSION` select in the `.pkb`, or the `import oracle.jdbc.OracleDriver;` in `NightlyRiskJob.java` — yet assertions 12, 16, and 17 depend on exactly those. A fresh agent following §6 would build fixtures that cannot support those assertions. (§3's layout does list `ignore.json` and `exemplars/`, but without contents.)
**Fix:** extend §6 with the `ignore.json` contents, the four exemplar files plus `pairs.json` (paths are cwd-relative — see G2), the `V$SESSION` line, and the `oracle.jdbc` import.

### G2 — `fixtures/exemplars/pairs.json` uses cwd-relative paths (fixtures)
**What is wrong:** `pairs.json` contains `"fixtures/exemplars/onprem/SimpleWriter.java"` etc. It works only when `learn_patterns.py` is run from the kit root; from any other cwd it prints `warning: pair-N paths missing, skipped` and writes empty maps. Assertions 16–18 implicitly assume kit-root cwd.
**Fix:** state the required working directory in §6/§4.16, or make the paths relative to the `pairs.json` file's own directory.

### G3 — §4.16: a `proposed` entry can never become `approved` on a later `--base` run (`scripts/learn_patterns.py`, `merge_list`)
**What is wrong:** on an existing `(from, to)` key, `merge_list` unions evidence, bumps `count`, and `continue`s — skipping the `--approve` status logic entirely. Verified: first run without `--approve` → all `proposed`; second run `--base prop.json --approve auto` on the same (unambiguous, vetted) pairs → still all `proposed`, 0 approved. The prompt says "`--base` merges … approved entries stay approved" but never states proposed entries are frozen; a rebuild following the prompt would reasonably re-evaluate status.
**Fix:** re-evaluate status for merged entries under `--approve` (promote when the rule is met), or state in the prompt that approval is decided only at first creation and later runs never change status.

### G4 — §4.13: "`CREATE TABLE` DDL is copied as-is, not translated" is false in code (`scripts/prepare_migration_tree.py`, `translate_plsql_file`)
**What is wrong:** `translate_plsql_file` applies `translate()` to the whole file and its per-statement loop includes `CREATE` (in `SQL_KEYWORDS`). The fixture DDL happens to contain no `SYSDATE`/`NVL`/etc., so the fixture does not expose this — but a `CREATE TABLE … DEFAULT SYSDATE` would be rewritten to `current_timestamp()`. Same for `CREATE VIEW` SELECTs.
**Fix:** either skip `CREATE TABLE` statements in `translate_plsql_file` (making the prompt true) or correct the prompt to "DDL statements are translated like any other statement; only the table *structure* is not converted to Delta".

### G5 — §4.16: `cloud_idioms` only comes from `--migrated-dir`, never from pair migrated files (`scripts/learn_patterns.py`)
**What is wrong:** the prompt describes `cloud_idioms` as "frequency counts of imports/calls in migrated code". The code initializes empty idioms unless `--migrated-dir` is given; `analyze_pair` results never feed idioms. Assertion 16 passes only because its command also passes `--migrated-dir fixtures/exemplars/migrated`; `--pairs` alone yields empty idioms.
**Fix:** feed pair migrated files into the idiom counts as well, or narrow the prompt to "from `--migrated-dir` only".

### G6 — §4.16/§4.17: `call_map` is derived from bare method names only; receiver info is discarded (`scripts/learn_patterns.py`, `prepare_migration_tree.py`)
**What is wrong:** §4.16 says derivation uses "method-name and receiver.method call shapes". The code does `ma, _ = java_calls(ta)` — the shapes set is thrown away — so `from` is e.g. `"commit"`, not `"writer.commit"`/`"Connection.commit"`. Consequence in `apply_java_patterns`: a call entry flags **any** `.<method>(` line regardless of receiver (e.g. `con.commit()` would be flagged by the `commit → null` pattern). The lib call-site flag has the same over-breadth: any line containing `\b<OldClass>\b` (including comments/strings) is flagged.
**Fix:** either derive `call_map.from` as `receiver.method` where the receiver type is known (and match on it in `apply_java_patterns`), or correct the prompt to "bare method names" and document the over-flagging as a known limitation.

### G7 — §4.16/§4.17: override application is case-sensitive while derivation is case-insensitive (`scripts/learn_patterns.py` vs `scripts/translate_sql.py`)
**What is wrong:** derivation compares `norm(translated).lower() != norm(migrated).lower()`; application requires a whitespace-normalized **exact** match. Verified: an override with `from: "SELECT a FROM t"` does not fire on `"select a from t"`. A learned override whose `from` differs only in case from a later statement silently never applies.
**Fix:** normalize case on both sides of the application match (or store overrides lowercased), and state the rule in §4.17.

### G8 — §4.13/§4.17: translation-report and bundle coverage gaps (`scripts/prepare_migration_tree.py`)
**What is wrong:** (a) The report never documents the stated limits (patterns split across fragments; DDL as-is) — no limits section exists in the report builder. (b) `stats['literals']`/`stats['translated']` count only Java literals; PL/SQL translations are not counted, though the report line reads "Literals translated by the first pass". (c) PL/SQL bundles have no top-level `patterns_applied` field — an approved SQL override applied to a `.sql` file is visible only inside `statements[].applied: ["pattern:sql-001"]`, while §4.17 says patterns are "recorded in the bundle as `patterns_applied`". (d) The PL/SQL header comment is only added when `items` is non-empty; a PL/SQL file with nothing to translate/flag gets no header at all, though §4.13 states PL/SQL files "get … a header comment".
**Fix:** add a limits section to the report; count PL/SQL statements in the translated total (or relabel the line); add top-level `patterns_applied` to PL/SQL bundles; always emit the header (or qualify the prompt with "when there is anything to report").

### G9 — §4.9: kinds list omits the `*_via_script` kinds (§4.9; `scripts/build_graph.py`, `scripts/build_table_catalog.py`)
**What is wrong:** §4.9 requires the README to document "the `read|write|read_via_proc|write_via_proc|call` kinds". The code also produces `read_via_script`/`write_via_script`, which neither the prompt nor the README's kinds list mentions (the README does mention them elsewhere, in the script-mediated-entry-points paragraph).
**Fix:** extend the kinds list in §4.9 (and the README) to `read|write|read_via_proc|write_via_proc|read_via_script|write_via_script|call`.

### G10 — §4.5: `call` db_objects omit `columns_read` / `columns_written` (`scripts/build_graph.py`)
**What is wrong:** the prompt says "`db_objects`: `[{name, kind, columns_read[], columns_written[]}]`". The code emits `{'name': p, 'kind': 'call'}` with no column keys at all (verified: `RISK_CALC_PKG.CALC_EXPOSURE` has neither key). Downstream code happens to use `.get()`, so nothing crashes today, but any consumer indexing `d['columns_read']` per the spec gets a `KeyError`.
**Fix:** emit the keys (empty lists) for `call` entries, or qualify the prompt with "column keys present except for `call` entries".

### G11 — §4.5: direct-read vs transitive-write precedence is unspecified and the code contradicts the stated rule's spirit (`scripts/build_graph.py`)
**What is wrong:** the prompt states only "Direct `write` beats transitive kinds" — no rule for direct-read vs transitive-write, or direct read+write. The code's Java aggregation sets `tables[t] = 'write'` unconditionally (erasing a direct read), and via-script/via-proc writes upgrade a direct `'read'` to `write_via_script` / `write_via_proc`. Verified: `NightlyRiskJob` directly reads `RISK_RESULTS` (`NVL(MAX(TOTAL_EXPOSURE)...)`), yet its kind is `write_via_proc`; the direct read survives only in `columns_read`. `RISK_POSITIONS` (directly read *and* written) shows kind `write` only. Docs/catalog recover this via the columns check, but the kind field misstates the direct evidence.
**Fix:** specify a full precedence table in §4.5 (e.g. direct read+write → `write` with `columns_read` non-empty; direct read + transitive write → keep `read` and record the transitive write separately) and make the code match it.

### G12 — §4.5: `--only` leaves stale `likely_depends_on` / `likely_required_by` in `units.json` (`scripts/build_graph.py`)
**What is wrong:** the prompt says "With `--only` … units.json keeps only them". The code filters units and prunes graph/catalog, but the per-unit `likely_*` lists are never pruned. Verified: `--only NIGHTLY_RISK_JOB` yields 1 unit whose `likely_required_by` still lists `main:com.risk.wm.jobs.RiskReportJob` — a unit absent from the file. (Secondary: the printed "N inferred" count is computed pre-pruning while nodes/edges/units are post-pruning.)
**Fix:** prune `likely_depends_on`/`likely_required_by` to the kept unit set (or to names still present), and compute/print the inferred count post-pruning.

### G13 — §4.5: catalog omits referenced-but-undefined objects (`scripts/build_graph.py`)
**What is wrong:** the prompt says "also create nodes for referenced-but-undefined objects" and describes `db-object-catalog.json` as `[{object, kind, defined_in[]}]`; §5's `db-object-catalog.csv` is supposed to "flag objects that are referenced but have no definition". The catalog is built only from `db_defs` (defined objects). Verified: the catalog contains only `RISK_CALC_PKG`, `RISK_POSITIONS`, `V_RISK_SUMMARY` — `RISK_RESULTS`, `RISK_AUDIT`, `V$SESSION` (all touched by units) are absent, so `--only` catalog pruning ("keeps only the objects they touch") is vacuous for them.
**Fix:** build the catalog from defined objects plus referenced-but-undefined ones (with `defined_in: []`), or narrow the prompt.

### G14 — §4.11: `--only` leaks DDL-only tables (`scripts/build_table_catalog.py`)
**What is wrong:** the prompt's `--only` means "only their tables are listed". The code computes `tables = sorted(set(readers) | set(writers) | set(ddl_cols))` where `ddl_cols` comes from the *full* `--plsql` file regardless of `--only`. Any table with DDL columns but untouched by the selected units still gets a section. Latent on the fixtures (only `RISK_POSITIONS` has DDL columns), but a rebuild following the prompt would filter `ddl_cols` too.
**Fix:** intersect `ddl_cols` with the selected units' tables when `--only` is given.

### G15 — §4.10: Java-to-Java "owning process" can only resolve within the same unit (`scripts/build_process_docs.py`)
**What is wrong:** the prompt says "Java-to-Java dependencies (imports of entry files, resolved to owning process via the `--java` records)". The code builds `path_to_unit` with `for u in [unit]:` — only the current unit; `render()` receives no units list, so an import owned by a *different* unit is mislabeled `(external / shared lib)`. It passes on fixtures only because `DbUtil` is in the nightly unit's own closure.
**Fix:** thread the full units list into the doc renderer and resolve against all units, or narrow the prompt to "resolved within the unit's own closure; other imports are labeled external".

### G16 — §4.5: script→script chaining ignores basename resolution (`scripts/build_graph.py`)
**What is wrong:** the prompt says "script→script chains followed, depth-safe via visited set" and elsewhere that scripts are "resolved by basename, since production paths in job commands rarely match checkout paths". The code resolves job→script by basename (`resolve_script`, with warning) and script→sql by basename — but script→script stores the raw `scripts_called` string as the node id (e.g. `script:/opt/risk/bin/env.sh`), which never matches a scanned script's `script:<checkout path>` id. The BFS therefore dead-ends on production paths. Latent on fixtures (`env.sh` isn't scanned), but inconsistent with the spec's own basename rule.
**Fix:** resolve script→script targets by basename through `resolve_script` like job→script, or state the limitation.

### G17 — §4.8: `config.yaml` thresholds are never wired to anything (`scripts/classify.py`, `scripts/run_process.py`)
**What is wrong:** §4.8 puts `db_heavy_ratio: 1.6, java_heavy_ratio: 0.6, min_evidence: 3` in `config.yaml`; `classify.py`'s docstring says "Thresholds come from config.yaml". Nothing in the kit reads `config.yaml` at all — `classify.py` reads only CLI flags (defaults coincidentally equal the config values), and `run_process.py` invokes it with no ratio flags. Behavior is correct today only because the defaults coincide; the prompt never specifies who loads the config.
**Fix:** either add a config-loading step (a script or the orchestrator reads `config.yaml` and passes the values) and specify it, or delete the thresholds from §4.8 and state that the CLI defaults are the source of truth.

### G18 — §4.1/§4.2: `oracle_isms` and `risky_constructs` output key names are unspecified (`scripts/scan_java.py`, `scripts/scan_plsql.py`)
**What is wrong:** §4.1 lists the features but never names the output keys; the code invents `SYSDATE_SYSTIMESTAMP`, `DUAL`, `NVL` (lumping NVL/NVL2), `OLD_JOIN_OP`, `CONNECT_BY`, `SEQUENCE`, `FOR_UPDATE`, `HINT`, `MODEL_CLAUSE`, `PIVOT`. Same for §4.2's risky constructs (`execute_immediate, dbms, utl, autonomous_txn, bulk_collect, forall, goto`). A rebuild would invent different keys, and §5's `oracle-dialect-census.csv` aggregates over these names, so the difference propagates downstream. §4.1 says "only nonzero" for `jdbc`/`oracle_isms` but §4.2 never says it for `risky_constructs` (code omits zeros); `metrics.loc` is never defined for PL/SQL (code: non-blank lines of comment-stripped, string-intact code).
**Fix:** add an explicit key-name table to §§4.1/4.2 (and the "only nonzero" rule plus the `loc` definition to §4.2).

### G19 — §4.1: `methods_rough`, `loc`, and `*` handling are underspecified (`scripts/scan_java.py`)
**What is wrong:** (a) "regex for method declarations" — the code's `METHOD_RE` requires a trailing `{`, so interface/abstract methods without a body are not counted. (b) `loc` is counted on string-stripped code, so a multi-line text block collapses to one line, whereas the prompt says "non-blank lines of comment-stripped code". (c) `*` is recorded only when there is exactly one FROM table; with multiple tables or a derived-table-only FROM it is silently dropped (never added to `columns_unattributed` either). `_from_tables` is depth-blind: for `SELECT * FROM (SELECT BOOK... FROM RISK_POSITIONS...)` it finds the *inner* `FROM RISK_POSITIONS` while scanning the outer FROM clause, so the outer `*` is attributed to `RISK_POSITIONS` — which is why assertion 9's `*` appears, accidentally rather than by the specified recursion. The `columns_unattributed` format (bare name vs `PREFIX.NAME`) is also unspecified; the code emits bare names for multi-table columns and `PREFIX.NAME` for unknown prefixes.
**Fix:** pin down the method regex, the `loc` basis (before/after string stripping), the `*` rules, and the `columns_unattributed` format in §4.1; make subquery attribution depth-aware in the code.

### G20 — §4.3: fallback FQN exclusions, conflicting-COMMAND winner, and retry semantics are unspecified (`scripts/scan_jobs.py`)
**What is wrong:** (a) the prompt says "else the first fully-qualified name with 2+ dots ending in an uppercase segment"; the code skips candidates containing `.jar` and starting with `java.`, `javax.`, or `org.` — a non-`java`-invoked command referencing an `org.*` main class (extremely common) would be skipped by the code but taken by a rebuild. The `java`-flag path has no such exclusion, so the two paths disagree. (b) Conflicting `COMMAND`: the prompt says "take the first non-blank value for scalar fields, and warn" but never says *which* command wins; the code picks `cmds[0]` from a **sorted** set — alphabetically first, not first-in-file. (c) Retry on `TASK_NAME + PARAMETERS`: the code retries only when *both* `main_class` and `scripts` are empty, and replaces the whole `info` dict — so jars found in `COMMAND` would be lost if the retry fires; the prompt's "If `COMMAND` yields no linkage" doesn't define whether script-only counts as linkage.
**Fix:** specify the exclusion list, the winner rule (first-in-file vs sorted), and the retry trigger/effect in §4.3.

### G21 — §4.4: `sqlplus` target match is not restricted to `sqlplus` (`scripts/scan_scripts.py`)
**What is wrong:** the prompt says "`sqlplus ... @file.sql` targets". The code's regex matches *any* `@file.sql` anywhere in the script (e.g. inside an `echo`), not only after `sqlplus`. Broader than specified; the fixture passes because its only `@...sql` is the real `sqlplus` line.
**Fix:** restrict the regex to `sqlplus` invocations, or widen the prompt to "any `@file.sql` reference".

### G22 — §4.2: no `ignored_table_columns` for PL/SQL (`scripts/scan_plsql.py`)
**What is wrong:** §4.2 says ignored reads/writes/calls are excluded from `ddl_columns`/`table_columns` and recorded in `ignored_tables[]`/`ignored_calls[]` — the code does that — but the ignored `table_columns` entries themselves are dropped silently, unlike `scan_java.py`, which records them in `ignored_table_columns{}`. The spec doesn't require the field here, so this is a spec gap (auditability loss), not a code bug.
**Fix:** add `ignored_table_columns{}` to the §4.2 output contract and the code, for parity with §4.1.

### G23 — §4.12: function-rewrite scoping rule is unstated (`scripts/translate_sql.py`)
**What is wrong:** the prompt's scoping sentence ("applied outside string literals and comments") is qualified "for keyword rules". The function rewrites (`NVL`, `TO_DATE`, `DECODE`, … via `_replace_calls`) run on the whole text — the code even comments this is "effectively never inside a string literal". Verified: `SELECT 'NVL(x,1)' FROM t` → `SELECT 'COALESCE(x, 1)' FROM t`, `applied: ['NVL->COALESCE']`. String-literal contents get rewritten.
**Fix:** state the scoping rule for function rewrites in §4.12 (they must also skip string literals) and fix the code to segment-scope them, or document the limitation.

### G24 — §4.2: `--exts` is required with no default; the prompt never says so (`scripts/scan_plsql.py`)
**What is wrong:** the code has `ap.add_argument('--exts', required=True)`. The prompt's CLI line shows `--exts ".sql,.pks,.pkb,..."` but never states required/optional/default, while `config.yaml` (§4.8) lists a `plsql_extensions` default — a rebuild could default it and behave differently, and running the script without `--exts` fails outright.
**Fix:** state "required, no default" in §4.2 (or give the code the config default).

---

## Nits

### N1 — Section numbering is broken (§4)
Two sections numbered **4.5** (`build_graph.py` and `classify.py`), §4.14 (`ignore_lists.py`) appears *after* §§4.15–4.17 although §§4.1/4.2/4.5/4.13 cross-reference "see 4.14", and §4.9 says "the 3-step run" while listing 4 steps (1. fill config, 2. scan, 3. review, 4. compile reports). Cross-references still resolve, but a rebuild following section order is confusing.
**Fix:** renumber sequentially (classify → 4.6, make_batches → 4.7, …), move `ignore_lists.py` to 4.5 or wherever it is first referenced, and change "3-step" to "4-step".

### N2 — `config.yaml` comment names the wrong workflow (`config.yaml:3`)
The file says run the `"oracle-migration-analysis"` workflow; line 50 of the same file, the README, and `run_process.py` all say `oracle-migration-review`.
**Fix:** change the comment to `oracle-migration-review`.

### N3 — README says "Three steps" but lists four (`README.md`, running section)
"Three steps, in order: 1. scan, 2. review, 3. reports" vs the actual Step 1 fill config, Step 2 scan, Step 3 review, Step 4 reports. Content required by §4.9 is all present; only the count label is off. README Step 4 also shows no command for the §5 compile outputs (see B1).
**Fix:** correct the count label and add the compile command once B1 is fixed.

### N4 — Assertion 9 column order: code sorts, prompt lists DDL order (§6, assertion 9)
The prompt says `DbUtil` reports `table_columns.RISK_POSITIONS.write = [POSITION_ID, BOOK, EXPOSURE, AS_OF_DATE]` (INSERT/DDL order). The code emits sorted values: `[AS_OF_DATE, BOOK, EXPOSURE, POSITION_ID]`. The assertion passes as a set but fails a strict ordered comparison; `ddl_columns` preserves DDL order, so the two fields disagree on ordering convention. `RISK_POSITIONS.read` also contains an extra `*` the assertion doesn't mention.
**Fix:** phrase the assertion as a set comparison (or list the sorted order), and state the ordering convention for each field in §4.1/§4.2.

### N5 — §4.14 wording: `load_ignore` "returns an empty list" (`scripts/ignore_lists.py`)
The prompt says "`load_ignore(path)` returns an empty list for a missing/empty path". The code correctly returns an empty `IgnoreList()`; a literal rebuild returning `[]` would crash callers (`[].ignore_object`). The `IgnoreList` API itself (`ignore_import`, `ignore_object`, `__bool__`) and `split_kept_ignored(names, predicate) → (sorted kept, sorted ignored)` match the spec exactly.
**Fix:** change the prompt to "returns an empty `IgnoreList`".

### N6 — §4.13 CLI header omits `--patterns`
The §4.13 usage line lists `--repos/--outdir/[--ignore]/[--only --units --graph --catalog]` with no `--patterns`, though the body and §4.17 describe it and the code has `--patterns` (default `''`). A header-only rebuild would omit the flag.
**Fix:** add `[--patterns patterns.json]` to the §4.13 header.

### N7 — §4.15 `run_process.py` CLI divergences (all permissive, undocumented)
`--jobs` is optional in code (writes an empty `jobs.json` if absent) though the §4.15 header shows it unbracketed; there is an extra `--plsql-exts` flag (default `.sql,.pks,.pkb,.fnc,.prc,.trg,.vw,.tab,.seq`) not in the spec; the default outdir `runs/<first-process>` replaces `:` with `_` (needed for `main:…` units) without the spec saying so. Steps, order, output layout, `--patterns` passthrough to the tree step only, and the final next-step message otherwise match §4.15 exactly.
**Fix:** bracket `--jobs` in the header and document `--plsql-exts` and the `:` → `_` rule.

### N8 — §4.16 `--pairs` is optional in code, required in the header
The header shows `--pairs pairs.json` unbracketed; the code defaults it to `''` ("either or both" per the module docstring). `--approve` defaults to `''` (no approval), which is what assertion 16's "without `--approve` all proposed" relies on.
**Fix:** bracket `--pairs` in the header and state that at least one of `--pairs` / `--migrated-dir` is required.

### N9 — §4.12 `split_top_commas` is paren-aware but not string-aware (`scripts/translate_sql.py`)
The prompt says DECODE uses "paren-aware arg splitting". The code counts parens but splits on commas inside quoted literals (e.g. `DECODE(a,'x,y',1,…)` would mis-split). Untested against fixtures; a latent correctness gap.
**Fix:** make the splitter string-aware, or note the limitation.

### N10 — CLI required/optional mismatches (§§4.5, 4.11)
§4.5 shows `--java/--plsql/--jobs` without brackets (required), but `build_graph.py` gives them `default=''` (optional). §4.11 shows `--plsql` as required; `build_table_catalog.py` has it `default=''` — omitting it silently degrades every column list to "observed in use". A rebuild making them required would fail loudly instead of silently degrading.
**Fix:** bracket the optional flags in the prompt, or make the code require them.

### N11 — §4.11 `tables.md` intro claims `*` marking that doesn't exist (`scripts/build_table_catalog.py`)
The header text says "Columns marked with `*` come from observed usage only (no DDL found, or the process reads `*`)". No column is ever suffixed with `*` in the no-DDL branch (verified in live `tables.md`); the only `*` appearing is the literal column name `*` recorded from `SELECT *`. The prompt itself doesn't require `*` marking — this is code-vs-its-own-doc drift.
**Fix:** remove the sentence or implement the marking.

### N12 — §4.7 vs `classify_process.md`: evidence-bundle source unnamed
The prompt says "load the unit's evidence bundle from `units.json`". The file says only "You are given a JSON evidence bundle for process `{unit_id}`" — `units.json` is never named. All other §4.7 items (definitions, 2–3 sentence summary, 3–5 objects, edge sanity-check, flag list, ONLY-JSON contract, low-confidence-over-guess) match exactly.
**Fix:** name `units.json` as the bundle source in the prompt file.

### N13 — §4.10 SVG Java layer shows entry points only (`scripts/build_process_docs.py`)
The prompt's layer list just says "Java". The code uses only `unit.entry_points` basenames (the nightly SVG shows `NightlyRiskJob.java`, not `DbUtil.java` from its closure). A rebuild could reasonably show all Java files.
**Fix:** specify "entry points only" (or "all closure files") in §4.10.

### N14 — Scanner nits (verified, low impact)
- `scan_java.py`: `commit` signal is zero-arg-only (`\bcommit\s*\(\s*\)` — a `commit(x)` call would be missed; the prompt gives no arg rule); `setSavepoint` also matches capital-`S` `SetSavepoint` (harmless extra); DUAL skip is exact-match only, so `SYS.DUAL` would be kept as a read table; `columns_unattributed` is never filtered by the ignore list (unattributed columns from ignored objects leak through); `ignored_tables` is the union of ignored reads+writes (prompt doesn't say union vs separate); `imports` preserves source order while tables/procs are sorted (unspecified).
- `scan_plsql.py`: call filtering also discards `EXECUTE.IMMEDIATE` and any call ending in `.IMMEDIATE` — a legitimate procedure literally named `FOO.IMMEDIATE(` would be dropped; `CALL_RE` requires a leading `[A-Za-z_]` while the prompt's pattern uses `\w`; `DEF_RE` additionally allows `IF NOT EXISTS` and quoted identifiers; the DDL skip list adds `KEY` to the prompt's list (safe expansions, but a strict rebuild would not have them).
- `scan_jobs.py`: the summary is two lines (autosys line has rows/dupes/jobs but no linkage count; main line has jobs/linkage but no rows/dupes; Control-M prints only the main line) and the duplicate count is stdout-only, not in JSON; condition parsing misses uppercase `SUCCESS(...)` and `s (NAME)` (space before paren), producing garbage like `SUCCESS(JOB`; the split path leaves inner spaces for `( FEED )`; Control-M CSV header matching is case-sensitive while Autosys uppercases keys (an uppercase-header Control-M CSV yields `job_name='UNKNOWN'`).
- `scan_scripts.py`: `scripts_called` matches any `*.sh|…` path anywhere (including inside `echo`); `SOURCE_RE` covers only `sh|ksh|bash` sourcing; `PYTHON_RE` misses flagged invocations (`python -u x.py`, `python3.11 x.py`); the `.jar` filter on `java_mains` can never fire (harmless).
**Fix:** pin each of these down in §§4.1–4.4 (or accept as documented behavior).

### N15 — Code with no spec (harmless extras a rebuild would not produce)
- `translate_sql.py`: `translate_statement()` wrapper; 1-arg `TO_DATE(x)`→`to_date(x)` and `TO_CHAR(x)`→`CAST(x AS STRING)`; extra format tokens `YY`, `HH12`; extra `TRUNC` units (`D`, `MON`/`MONTH`, `YY`/`YEAR`, `HH`).
- `learn_patterns.py`: output adds a `generated_from` block and a `mechanical` field on SQL overrides beyond the §4.16 schema.
- `prepare_migration_tree.py`: `chain_sql` truncated to 1000 chars, PL/SQL statement originals/translations truncated to 2000 chars; a missing `--patterns` file is silently treated as `{}` with no warning; `--only` without `--units/--graph/--catalog` silently mirrors nothing (only a stderr warning for unknown unit IDs).
- `build_graph.py`: warns to stderr for `--only` units not found; synthetic job dicts omit `job_id`/`task_name`/`parameters`; synthetic creation takes `classes[:1]` only (a multi-class file with two mains yields one synthetic unit).
- `build_process_docs.py`: default label `UNLABELLED` when no preclassification exists; unattributed columns are surfaced in the Review section when unreviewed.
- `build_graph.py` linkage: `'ok'` requires `exact_fqns or script_fqns` — a job whose script resolves by basename but whose Java mains aren't in the index gets `'missing'`, while the prompt's "'ok' (exact FQN or script-resolved match)" could be read as script-resolution alone sufficing. Ambiguous wording, untestable on fixtures.
**Fix:** fold the intended ones into the spec; delete or document the rest.

---

## Assertion-by-assertion results (verified by execution in `/tmp`)

| # | Result | Note |
|---|--------|------|
| 1 | PASS | ROWNUM/SYSDATE/NVL found, `{call}` proc, `tables_read=[RISK_POSITIONS, RISK_RESULTS]`, DbUtil import, `has_main`. Caveat: SYSDATE is keyed `SYSDATE_SYSTIMESTAMP` (G18). |
| 2 | PASS | TABLE/VIEW/PACKAGE BODY, `RISK_RESULTS` under writes, `EXECUTE IMMEDIATE` under risky not calls. |
| 3 | PASS | 7 rows, 1 duplicate dropped, 5 jobs, both conditions aggregated, Control-M XML parses. |
| 4 | **FAIL** | "Two main() classes" — there are three; edge exists but only for the synthetic unit in the `jobs: []` run (B4). |
| 5 | PASS | Nightly `DB_HEAVY` (allowed: "BALANCED or DB_HEAVY"); script-only jobs `NEEDS_REVIEW`. |
| 6 | PASS | Checkpoint exclusion and `--only-labels` verified live. |
| 7 | PASS | Java main, `risk-dl.jar`, `env.sh`, `post_load.sql` all found. |
| 8 | PASS | `WRAPPED_JOB` `ok`, nightly's files, `RISK_AUDIT` `write_via_script`; `LEGACY_JOB` `maybe` + `['LegacyJob']`; synthetic report unit, no duplication. |
| 9 | PASS as sets | Column sets correct; ordered comparison fails (N4); `*` attribution is accidental (G19). |
| 10 | PASS | 6 HTML + index, DRAFT, chain, Inputs with `RISK_POSITIONS`, inline SVG — but the SVG's upstream/downstream layers are inverted (B6). |
| 11 | PASS | NVL→COALESCE, SYSDATE→`current_timestamp()`, TO_DATE, DECODE, ROWNUM needs-review, tree rewrites + TODO marker + bundles + report. |
| 12 | PASS | `oracle.jdbc.OracleDriver` → `ignored_imports`; `DBMS_OUTPUT.PUT_LINE` → `ignored_calls`; `V$SESSION` → `ignored_tables`; no `dbms` in risky; DBMS_OUTPUT unflagged, ROWNUM still flagged. |
| 13 | PASS | Without `--ignore` everything is still traced. |
| 14 | **FAIL** | Fails as specified (missing `--repos`, 4 files not 6, no `RISK_AUDIT`); tables list contradicts the `RISK_AUDIT` clause (B2). |
| 15 | **FAIL** | "8-file tree" is 7 files with the three §1 repos (B3). |
| 16 | PASS | Exactly 1 approved library mapping, 1 approved call mapping (`commit` → `null`), 1 approved SQL override, idioms list `AdlsWriter`; without `--approve` all `proposed`. |
| 17 | PASS | Import rewritten, `new OracleWriter(...)` and `writer.commit()` flagged, MERGE via `pattern:sql-001` with no flags; without `--patterns` import unchanged, MERGE flagged. |
| 18 | PASS | `--base` re-run keeps the same three entries, counts at 1, approved intact. |

**14 of 18 pass as written; 3 fail (4, 14, 15); 1 passes only as a set comparison (9).** Note that passing assertions do not cover the scanner blockers B10–B13: the fixtures contain no `SELECT...INTO`, `TABLE(`, non-`{call` `prepareCall`, or text-block-concatenation case.

## What passed as specified (spot-verified)

- `classify.py` formula, thresholds, and defaults match §4.5 exactly.
- `make_batches.py` sanitize rule, checkpoint exclusion, `--only-labels`, `--only`, `--limit` all match §4.6.
- `classify_process.md` matches §4.7 field-for-field, including the patterns item 7.
- `translate_sql.py` core rules (NVL/NVL2, SYSDATE, DUAL, hints aside from B8, TO_DATE/TO_CHAR masks, TRUNC, DECODE), all flags, ignored-DBMS suppression, and override exact-match behavior match §4.12.
- `learn_patterns.py` derivation and `--approve auto`/`--base` behavior match §4.16 except B7/G3/G5/G6/G7.
- `config.yaml` keys/values match the §4.8 block exactly.
- The README covers everything §4.9 requires.

---

## Prioritized fix list

1. **Fix assertions 4, 14, 15 and pin the commands** (B2–B4): correct "two" → three synthetic units with explicit IDs; give the exact `run_process.py` command including the scripts repo (or move `run_nightly.sh` into a repo); correct the tables list to include `RISK_AUDIT`.
2. **Specify the §5 compile step** (B1): add a `compile_reports.py` spec with exact schemas, or rewrite it as an explicit agent/manual step.
3. **Decide truth for the code-vs-spec blockers and align one side** (B5–B15): via-proc columns, SVG inversion, `--approve auto` ≥3 rule + merge dedup, hint removal, TO_DATE confidence, `INTO` guard, `TABLE(` pattern, dead `prepareCall`, text-block merging, Control-M fields and XML tag match. For each, either fix the code or correct the prompt — do not leave them disagreeing.
4. **Complete the §6 fixture specs** (G1–G2): `ignore.json` contents, the four exemplar files + `pairs.json`, the `V$SESSION` line, the `oracle.jdbc` import, and the required working directory.
5. **Renumber and tidy** (N1–N3): fix the duplicate 4.5, move 4.14 into sequence, "3-step" → "4-step", config comment workflow name.
6. **Close the remaining gaps** (G3–G24): `--base` re-approval, DDL as-is, `cloud_idioms` source, bare-method call maps, override case-sensitivity, report/bundle coverage, kinds list, `call` column keys, precedence table, `--only` staleness, catalog completeness, DDL leak, cross-unit Java resolution, script→script basename, config wiring, key-name tables, `methods_rough`/`loc`/`*` rules, scan_jobs underspecification, `sqlplus` scope, `ignored_table_columns` parity, function-rewrite scoping, `--exts` required.
7. **Sweep the nits** (N4–N15): assertion 9 ordering, `load_ignore` wording, `--patterns` in the §4.13 header, `run_process`/`learn_patterns` CLI headers, `split_top_commas` string-awareness, CLI required/optional flags, `tables.md` `*` sentence, `units.json` naming, SVG Java layer, scanner edge cases, and the undocumented extras.

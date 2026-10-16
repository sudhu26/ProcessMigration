# Phase-2 agent prompt — process classification review

You are reviewing ONE batch process from a Java + Oracle estate being migrated to
Azure Databricks (Java kept, Oracle replaced by Spark SQL / Delta Lake).

## Input

You are given a JSON evidence bundle for process `{unit_id}` containing:
- the Control-M job definition (command line, schedule, declared dependencies),
- the Java entry point(s) the job invokes and their scanner-extracted signals
  (JDBC call sites, SQL string literals, stored-procedure calls, Oracle dialect
  features, imports, LOC, control-flow counts),
- the database objects touched (tables/views/packages/procedures) and how
  (read / write / call),
- the deterministic pre-classification and its score breakdown.

## Task

1. Read the evidence. If it is thin or contradictory, read the actual source files
   listed in the bundle (paths are given) — but stay focused: entry point first,
   then the 2–3 files with the strongest signals. Do not go spelunking through
   the whole repo.
2. Decide the classification:
   - `DB_HEAVY` — the process's behavior is dominated by database work: complex SQL,
     stored-procedure calls, set-based transforms, heavy Oracle-dialect usage.
     Migration risk concentrates in SQL/PLSQL conversion.
   - `JAVA_HEAVY` — the behavior is dominated by Java logic: computation,
     branching, file handling, orchestration; the database is mostly dumb storage.
     Migration risk concentrates in JDBC/Spark semantics and I/O paths.
   - `BALANCED` — both matter materially.
   - `UNKNOWN` — the evidence is insufficient to decide (say exactly what is missing).
3. Write a 2–3 sentence plain-language summary of what the process does.
4. List the 3–5 database objects that matter most for migration (with why, briefly).
5. Sanity-check the `likely_depends_on` / `likely_required_by` fields in the
   evidence bundle (data-flow-inferred ordering edges): confirm the plausible
   ones, call out the wrong ones, and note any real ordering dependency the
   inference missed.
6. Flag anything a human must look at: transaction/rollback reliance, Oracle-only
   features with no Spark equivalent, dynamic SQL built at runtime, ambiguous
   job→code linkage, evidence gaps.

## Output contract

Return ONLY a JSON object (no markdown fences, no commentary) with exactly:

{
  "unit_id": "<same id>",
  "verdict": "DB_HEAVY | JAVA_HEAVY | BALANCED | UNKNOWN",
  "confidence": "high | medium | low",
  "agree_with_preclassification": true,
  "summary": "<2-3 sentences>",
  "key_db_objects": [{"object": "<name>", "why": "<one line>"}],
  "migration_notes": ["<short note>", ...],
  "human_review_flags": ["<short flag>", ...],
  "evidence_gaps": ["<what was missing, if anything>"]
}

Be honest about uncertainty: a low-confidence verdict with clear gaps beats a
confident guess. Never invent database objects or behaviors not supported by the
evidence or the source files you actually read.

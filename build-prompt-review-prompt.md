# Prompt: independent 4-eye review of a rebuild spec

Copy everything below the line and paste it into the reviewing tool, together
with the two attachments: `migration-kit-build-prompt.md` (the spec under
review) and `migration-kit.zip` (the actual kit — ground truth). If either
attachment is missing, say so before reviewing.

---

You are an independent reviewer. You were not involved in building this work.
Do not trust any summary of it — verify everything against the actual files.

## Inputs

1. `migration-kit-build-prompt.md` — the rebuild spec under review.
2. `migration-kit/` — the actual kit the spec describes (Python scripts,
   fixtures, config.yaml, README.md, prompts/). This is ground truth: where
   the spec and the code disagree, the code is right and the spec is wrong.

## Context (what the kit is)

An analysis kit for migrating a 15-year-old Java + Oracle risk platform
(~4,000 Autosys batch jobs, 3 repos: Risk_DB for Oracle code, Risk_DL for
process + shared code, CoreWM for most Java) to Azure Databricks — keep Java,
replace Oracle with Spark SQL. Design rules the spec states: deterministic
first (stdlib-only Python does the heavy lifting; the LLM is used only for
per-process review of pre-built evidence bundles, never for bulk code
reading), checkpoint/resume for long runs, honest uncertainty (anything the
evidence cannot support is labelled NEEDS_REVIEW, never invented).

## Review dimensions

1. **Completeness.** Does every script and file in the kit have a matching
   spec in the prompt? Does everything the prompt specifies actually exist
   in the kit? List anything missing on either side.
2. **Accuracy.** Do the specs match the actual code? Check CLI flags and
   defaults, input/output JSON shapes, and described behaviours. Pay extra
   attention to the newest features: `learn_patterns.py` (pattern learning
   from migrated exemplars), `--patterns` in `prepare_migration_tree.py`
   and `run_process.py`, `--only` on the graph/doc/catalog/batch/tree
   scripts, `run_process.py` itself, ignore lists, wrapper-script
   association, script → SQL-file edges, the 3-hop graph pruning, and
   column extraction.
3. **Self-containment.** Could a fresh agent with no prior context rebuild
   this kit from the prompt alone? Flag anything that relies on knowledge
   not stated in the prompt.
4. **Testability.** The prompt ends with 18 self-test assertions. Is each
   one executable and verifiable from the fixtures provided? Flag any
   assertion that is vague enough to pass while the feature is broken, or
   that the fixtures cannot actually support.
5. **Contradictions.** Counts, names, paths, flag names, file locations —
   anything the prompt says in one section and contradicts in another.
6. **Gaps.** Kit behaviours with no spec at all: edge cases, error and
   warning paths, ambiguous points a rebuilding agent would have to guess.
7. **Divergence risk.** Where would a rebuilding agent most likely produce
   something different from the original kit? Rank the top three.

## Method

- Read the prompt fully, then read the code. Verify, don't assume.
- Where possible, trace each self-test assertion to the fixture and code
  that would satisfy it, and say which ones you could not verify and why.
- Do not propose redesigns or new features. The question is only whether
  the spec faithfully describes this kit.

## Output format

- **Verdict first:** one line — `Ready to use` or `Needs fixes first` —
  plus one paragraph saying why.
- **Findings:** one item per issue, each with severity (`Blocker` /
  `Gap` / `Nit`), location (prompt section + kit file), what is wrong,
  and a concrete fix (the exact wording or change, not just "clarify").
- **Prioritized fix list:** the smallest set of edits that makes the
  prompt ready, in the order they should be applied.
- **What's good:** two or three lines on what the prompt already does
  well, so the author knows what not to break while fixing the rest.

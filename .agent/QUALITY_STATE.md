# QUALITY STATE — Llama Orchestrator Deep Analysis

**Date:** 2026-09-05 (UTC)
**Goal id:** goal-643e8744-61d9-49bb-bbb9-010fce534269
**Objective:** Deep analysis of `infra-local/llama-orchestrator`; identify bugs/inaccuracies with evidence; produce a spec-driven implementation plan as a NEW Markdown file. No other files may be altered.

## Status: COMPLETE — deliverable written, independently reviewed, corrections applied, regression clean

### Completed (this goal)
1. Evidence gathered on pristine HEAD copy (`/tmp/lo-src` = `git archive HEAD`; venv `/tmp/lo-analysis-venv`, Python 3.11.2): **ruff 1157** (856 auto-fixable), **mypy --strict 195 errors in 41 files**, **pytest 20 failed / 588 passed** (one later full run: 19/589 — the delta is exactly `test_v2_locking::TestInstanceLockManager::test_lock_timeout`, proving F3 flakiness). Logs: `/tmp/lo-{ruff,pytest,mypy}.log`.
2. All module reads complete: engine/*, health/*, binaries/*, daemon/service.py, config/{__init__,validator,schema}, runtime_args.py + config_validator.py + conftest.py (via extract), gui/app.py thread model (directly readable).
3. **19 confirmed defects** (F1–F19) + ~10 suspect design concerns with file:line evidence → `.agent/lo-findings.md`.
4. Live repros (venv): F2 tarfile TypeError; F15 parser misclassification (`--parallel 2` value swallowed; `validate_runtime_arg_alignment` returns 0 issues on the vulkan+`--device CUDA1` repro; `find_conflicts` → `[]`); F3 both inversion directions via `/tmp/lo-lock-repro.py` (fresh dead-owner lock → stale=True; 7200 s-old live-owner lock → stale=True).
5. **Deliverable written:** `LO_IMPLEMENTATION_PLAN_2026-09-05.md` at project root — spec-driven: §3 defect catalog, §4 requirements R1–R26 with acceptance criteria, §5 design decisions, §6 six-phase task plan (≈16 E), §7 risks, §8 definition of done.

### Verified environment facts
- Project = git submodule, HEAD `b51ab3c`; 314 tracked files already `M` vs HEAD **before** this session began (pre-existing working-tree drift — not caused by this analysis; confirmed: no tracked file modified by us, only the 3 new artifact paths below are untracked additions).
- Access quirk: 22 files mode 0600 uid 10000 unreadable → analyzed via HEAD-blob copies in `tmp/lo-analysis-extract/` (verified identical to HEAD; list in `tmp/lo-analysis-extract/unreadable.txt`). `gui/app.py` and `daemon/service.py` directly readable despite unusual ownership.
- `bins/69d67021-…` untracked symlink dated Aug 4 → user's ROCm build, pre-existing, untouched.

### New artifacts (the ONLY additions to the live tree)
- `.agent/lo-findings.md`, `.agent/QUALITY_STATE.md`
- `LO_IMPLEMENTATION_PLAN_2026-09-05.md`
- `tmp/lo-analysis-extract/**` (HEAD-blob copies + unreadable.txt)

### Decisions
- D1: Analyze from HEAD blobs where unreadable (no drift detected).
- D2: Evidence via /tmp venv + HEAD archive copy; re-run commands recorded in findings header.
- D3: Final plan = one new root-level MD `LO_IMPLEMENTATION_PLAN_2026-09-05.md`, spec-driven (requirements → design → tasks), each defect a requirement with acceptance criteria.
- D4: F3 description corrected after deterministic repro: age branch runs unconditionally (OR, no grace), so aged locks of live owners ARE reclaimed; flakiness comes from the dead-PID branch + the test's `pid+1` assumption. Plan + findings updated accordingly.

### Independent adversarial review (subagent 137a6fa9) — COMPLETE
- Verdicts: A(F1), B(F2), C(F15), D(F3), E(plan consistency R1–R26↔F1–F19, all S1/S2 mapped to req+task), F(no overstatement; zero-reference claims re-grepped) — **all CONFIRMED**. No material omissions found.
- Reviewer flagged 4 factual corrections + 1 T1.5 nuance. Each was independently re-verified against /tmp/lo-src before applying: (1) F2 buggy call is at downloader.py:**389** (:358 is the def line); (2) F15 counts → 277 of 404 carry the `sorta` recursive pattern, 264 nested, longest entry 1108 chars with `slot-id` ×135; (3) `_is_lock_stale` spans **80–113** (`acquire` at :130); (4) findings module index "~4.5k lines" → 781. All applied to plan + findings, including a sweep that caught two residual stale spots (plan decision #4; findings item 15). T1.5 now also requires `--flag=value` round-trip consistency tests (assignments vs named/extra).

### Final validation (post-review, executed this span)
1. **pytest re-run** (pristine HEAD copy `/tmp/lo-src`, venv py3.11.2): 19 failed / 589 passed — every failure in a documented root-cause group (detection×5, memory_fit×11, benchmark×2, `test_extract_tar_gz` F2); `test_lock_timeout` passed this run = the documented F3 flake signature (it was the sole delta of the earlier 20F/588P baseline). No new regressions.
2. **ruff re-run:** exactly 1157 errors (856 fixable) — matches plan §1/§2.
3. **Deliverable structural review:** sections 1–8 present; R1–R26 all defined and defect/suspect-tagged; F1–F19 all map to ≥1 requirement; D1–D8 all traceable (added `[D8]` tag to R23, which already specified the downloader.py:139 fix); every T-task references a requirement; stale-figure sweep clean.
4. **Git regression:** 314 pre-existing `M` drift entries unchanged; exactly 4 untracked artifact entries (`.agent/`, plan MD, `tmp/`, pre-existing `bins/` symlink); `state/`+`logs/` untouched; no tracked file modified by this work.

### Open items before goal completion
- [x] Independent adversarial review of plan vs. evidence — CONFIRMED, corrections applied.
- [x] Final regression: `git status --porcelain` shows only pre-existing `M` drift (314 files, incl. `.hermes/plans/2026-06-17_153000-app-refactor.md`) + exactly the 4 untracked artifact entries (`.agent/`, plan MD, `tmp/`, pre-existing `bins/` symlink); zero tracked-file modifications by this work; `state/`/`logs/` untouched.

### Next actions
1. Final git-status regression check (below).
2. Complete goal after it passes.

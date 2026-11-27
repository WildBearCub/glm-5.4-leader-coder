# AGENTS.md addition for the Leader pattern

Paste this section into the project's `AGENTS.md`. Any agent that reads it gets the same circuit breaker. Put it at the top of the file, before project-specific rules.

## 分叉前先对齐 · Discuss Before Diverging（2026-11-24 用户令写入；英文版省 token、与系统提示词术语一致）

- **Hard circuit breaker on reasoning & scouting**: When a request leaves ≥ 2 plausible product, scope, or architectural interpretations:
  1. **Stop simulating branches in `<thought>`**: State each plausible fork and its core trade-off in 1 sentence — do NOT mentally design implementation details, edge cases, or speculative refactors for unchosen paths.
  2. **Cap pre-alignment scouting to 1 pass**: Run at most ≤ 3 direct file reads or 1 parallel read-only subagent batch (`Explore`) to verify codebase facts before asking. *(Full parallel scouting for unambiguous tasks remains unrestricted.)*
  3. **Zero unrequested scope**: Never invent hypothetical future requirements or bundle "while we're here" cleanups.
- **Single-turn check-in (overrides the `brainstorming` skill's multi-turn/no-menu rule)**: Unless the user explicitly asks for open brainstorming or an interactive design interview, share known codebase facts in 2–3 sentences and resolve ambiguity in **one** check-in (merging any model or execution-mode recommendation into the same check-in):
  - **Finite choices (approaches, scope boundary, execution mode)**: Call the structured question tool (`AskUserQuestion`) with 2–4 concrete options, `(Recommended)` first.
  - **Open intent ("what goal do you want?")**: Ask 1 concise plain-text question without steering options.

## Verify the Minimum · 最小验证（2026-11-27 user decree; goes right under the section above, still before project rules）

- **Stop at the verdict**: after each save run only the affected test file plus the typecheck. Never re-run a known-green baseline.
- **Tests pin only the rules the dispatch names** — one rule, one assertion, no edge-case sprawl. Cap per unit: ≤ 15 new assertions, ≤ (acceptance clauses + 3) end-to-end checks. New features leave no "red on old code" evidence (bug fixes do, once); never manufacture red evidence by breaking behaviour on purpose.
- **The full regression chain belongs to the Lead**: workers run it zero times unless the dispatch names it (then once). The Lead runs it once per batch, on an idle machine.
- **Measure each number at most twice** (worker once, Lead once on an idle machine); a reading taken on a busy machine is a lead, not a verdict, and is not re-chased.
- **Reports stay short**: low-risk units list files + commands + raw numbers; the requirement-by-requirement self-cert table is for high-risk units only.

## Keep the To-do List Small · 待办清单（2026-11-27 user decree; goes at the top with the sections above）

- **At most 6 items, ever**, and only one in progress. The list shows what is in flight now, not the whole campaign: before a seventh arrives, merge related items or move finished ones to the task ledger / notes file.
- **One plain sentence each**: say what is being done and what comes next, as if to someone who has never seen the code. No ticket ids, no internal labels (gate names, branch names, file handles), no shorthand only you understand.
- A completed item leaves the list as soon as it is reported — the ledger holds the history.

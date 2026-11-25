# AGENTS.md addition for the Leader pattern

Paste this section into the project's `AGENTS.md`. Any agent that reads it (Claude Code / Codex / Cursor / ZCode) gets the same circuit breaker. Put it at the top of the file, before project-specific rules.

## 分叉前先对齐 · Discuss Before Diverging（2026-11-24 用户令写入；英文版省 token、与系统提示词术语一致）

- **Hard circuit breaker on reasoning & scouting**: When a request leaves ≥ 2 plausible product, scope, or architectural interpretations:
  1. **Stop simulating branches in `<thought>`**: State each plausible fork and its core trade-off in 1 sentence — do NOT mentally design implementation details, edge cases, or speculative refactors for unchosen paths.
  2. **Cap pre-alignment scouting to 1 pass**: Run at most ≤ 3 direct file reads or 1 parallel read-only subagent batch (`Explore`) to verify codebase facts before asking. *(Full parallel scouting for unambiguous tasks remains unrestricted.)*
  3. **Zero unrequested scope**: Never invent hypothetical future requirements or bundle "while we're here" cleanups.
- **Single-turn check-in (overrides the `brainstorming` skill's multi-turn/no-menu rule)**: Unless the user explicitly asks for open brainstorming or an interactive design interview, share known codebase facts in 2–3 sentences and resolve ambiguity in **one** check-in (merging any model or execution-mode recommendation into the same check-in):
  - **Finite choices (approaches, scope boundary, execution mode)**: Call the structured question tool (`AskUserQuestion`) with 2–4 concrete options, `(Recommended)` first.
  - **Open intent ("what goal do you want?")**: Ask 1 concise plain-text question without steering options.

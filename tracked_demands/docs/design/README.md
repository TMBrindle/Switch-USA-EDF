# Tracked demands: April 2026 design documents

These are the original design documents for the tracked demands module (MOD-002), copied here for reference. They
were written before the module was built and are not kept up to date.

| File | What it is | Last modified (UTC) |
|---|---|---|
| `spec_v1.md` | Module specification v1.0: sets, parameters, variables, constraints, input and output files | 2026-04-15 14:11 |
| `design_decisions.md` | Design decisions register, with the open items at the time | 2026-04-15 14:11 |
| `HANDOFF.md` | Staged implementation plan (stages 1–10) | 2026-04-15 14:11 |

**Source:** `docs/plans/planned_mods/MOD-002_tracked_demands/` in the VM's untracked `docs/` folder (main tree of
Switch-USA-PG-ReEDS). That folder is not in the repository. Every copy found on the VM was identical. The files
are copied unchanged; none held personal names or other personal identifiers. Since then (11 Oct 2026, on
`tom/tracked-demands-v2`), `design_decisions.md` has a dated section of the 11 October decisions at the end, and
one-line "Superseded" markers were added at the April items those decisions replace (in `design_decisions.md` and
`spec_v1.md` §5.12 and §8). The April text is otherwise unchanged.

**Original names.** `spec_v1.md` was `tracked_demands_final_spec_v1.md`, and `HANDOFF.md` still refers to it
by that name. `design_decisions.md` was `tracked_demands_design_decisions.md`, and `HANDOFF.md` was
`tracked_demands_claude_code_handoff.md`.

**Superseded in part.** The decisions of 11 October 2026 (CHANGES §108 on `tom/tracked-demands-v2`) supersede
parts of these documents. Where they differ, §108 and the current guide (`Guides and documentation/tracked_demands.md`)
take precedence. The documents also disagree with each other in one place: the default for on-site emissions in the
system cap is False in `spec_v1.md` (§3.1, §5.12, decision 10) and recommended True in `design_decisions.md` (open
item 5).

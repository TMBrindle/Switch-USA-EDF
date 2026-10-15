# v3.2 run cards (handoff to the VM runner)

| File | What |
|---|---|
| `recipe_L.md` | Before any v3.2 build: offshore split, cluster cache, PowerGenome import |
| `flagsoff_build2.md` | F2: flags-off build with PowerGenome from the submodule (c4c433c) |
| `br_twostage.md` | **First:** the build-rate package's two-stage test (2028 + 2030). It gates S0 v3.2 final (§247) |
| `s0_v32.md` | The S0 v3.2 **final** chain (root `s0prod_A_v32final`; §248), after its smoke build (section 2a; §250) |
| `RESUME.md` | After an interruption (VM shut down 14 Oct): assess, verify, clear partials, resume S0 v3.2 without rebuilding (§243) |
| `bill_central.md`, `txonly.md`, `s1.md`, `s4.md`, `s2.md`, `s5.md`, `s3.md` | Scenario chains, in run order |
| `READY_S0_V32` | One line: the commit the S0 v3.2 chain (and F2) must run at. Present = recipe L and the S0 card are final |
| `READY_SCENARIOS` | One line per scenario card: `<card> <commit>`. Written only once those cards are final; a card not listed must not run |

Commits that touch only this folder change nothing a build reads, and the reuse code check exempts them (CHANGES §135).
A READY file is written only when its cards are final; the runner acts on it without further review.


**History:** both READY files were withdrawn in CHANGES §139 (the 2028 day selection failed at 0c9d9f7) and re-issued
together in §240, at the commit with the day-selection fix.

**READY re-issued for RESUME.md (§243):** the commit differs from 4894d6a only in files the reuse code check exempts
(the resume script, tests, cards). It means "check out and resume", not "rebuild". The S0 v3.2 inputs built at
02:40 UTC on 14 Oct stand.

**Run order at the current READY commit (§248, §250):**
1. `br_twostage.md`. A test already run at 5d2e60a counts (the card says why). If it fails, STOP.
2. `s0_v32.md` section 2a: the smoke build (2028 + 2035, scratch root `s0prod_A_v32smoke`, no solve; about 45–60 min).
   A hard gate: if it fails, STOP.
3. `s0_v32.md` from section 3 (S0 v3.2 final).
4. The `READY_SCENARIOS` queue: BILL_central, txonly, S1, S4, S2, S5, S3.

**Standing rule (§250, from 15 Oct 2026):** any change to what PowerGenome reads (settings, data, the inputs it is
given) or to the case writer (`pg_to_switch.py`, `s0_workflow/`) gets a VM smoke build before any full run: the first
stage and the first stage the change affects (for S0: 2028 and 2035) into a scratch root, all pre-solve checks, no
solve. A READY re-issue for such a change names the smoke step in its card. Cloud tests don't replace it: the cloud has
no PowerGenome data and doesn't read the PowerGenome clone.

**Labels:**
- **"S0 v3.2 pre"** is the run built at 4894d6a on 14 Oct (root `s0prod_A_v32`).
- **"S0 v3.2 final"** is this card set's run. The scenarios reuse from it, and any scenario run made against pre is
  labelled "pre".

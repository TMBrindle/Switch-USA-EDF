# v3.2 run cards (handoff to the VM runner)

| File | What |
|---|---|
| `recipe_L.md` | Before any v3.2 build: offshore split, cluster cache, PowerGenome import |
| `flagsoff_build2.md` | F2: flags-off build with PowerGenome from the submodule (c4c433c) |
| `s0_v32.md` | The S0 v3.2 chain |
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

# v3.2 run cards (handoff to the VM runner)

| File | What |
|---|---|
| `recipe_L.md` | Before any v3.2 build: offshore split, cluster cache, PowerGenome import |
| `flagsoff_build2.md` | F2: flags-off build with PowerGenome from the submodule (c4c433c) |
| `s0_v32.md` | The S0 v3.2 chain |
| `bill_central.md`, `txonly.md`, `s1.md`, `s4.md`, `s2.md`, `s5.md`, `s3.md` | Scenario chains, in run order |
| `READY_S0_V32` | One line: the commit the S0 v3.2 chain (and F2) must run at. Present = recipe L and the S0 card are final |
| `READY_SCENARIOS` | One line per scenario card: `<card> <commit>`. Written only once those cards are final; a card not listed must not run |

Commits that touch only this folder change nothing a build reads, and the reuse code check exempts them (CHANGES §135).
A READY file is written only when its cards are final; the runner acts on it without further review.

**Withdrawn (CHANGES §139):** both READY files, while the S0 v3.2 2028 day selection fails at 0c9d9f7. They come back
together, at one commit, once the cause is fixed. Until then no card in this folder runs; only the diagnostic build in
CHANGES §139 does.

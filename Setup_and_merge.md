# Contributing to the EDF Switch Repository

This guide walks you through two things:
1. **Setting up your local copy** of the EDF Switch fork
2. **Migrating your existing local changes** into the shared repo

---

## Key concepts

Before starting, it helps to understand a few key terms:

- **Fork** — a copy of the original Switch-USA-PG-ReEDS repository that lives on GitHub under the EDF team's account. This is where all EDF-specific modifications are stored. The original Switch repo and the EDF fork exist independently on GitHub.
- **Clone** — a copy of a GitHub repository downloaded to your local machine. You do your actual work in the clone, then push changes back up to GitHub.
- **Branch** — a parallel version of the code within a repository. Branches let multiple people work independently without overwriting each other's work. Think of `edf-baseline` as the shared team version, and your personal branch as your own working copy of it.
- **Commit** — a saved snapshot of your changes in your local clone. Commits are local until you push them.
- **Push** — sending your local commits up to GitHub so others can see them.
- **Pull** — downloading changes from GitHub into your local clone.
- **Checkout** — switching your local working copy to a different branch. The files in your folder update to reflect that branch's contents.

---

## Part 1: Setting up your local copy of the EDF fork

---

### Step 1: Clone the EDF fork

Cloning downloads the repository from GitHub to your local machine and sets up a connection back to GitHub (called `origin`) so you can push and pull changes later.

Open a terminal, navigate to where you want to store the repo, and run:

```
git clone https://github.com/TMBrindle/Switch-USA-EDF.git
cd Switch-USA-EDF
```

After this you'll have a local folder called `Switch-USA-EDF` containing the full repository.

**If you are using VS Code:** open a new VS Code window and use **File → Open Folder** to navigate to and open the `Switch-USA-EDF` folder. This ensures VS Code is working in the right root directory. You can then open a terminal inside VS Code using **Terminal → New Terminal** and continue the remaining steps from there — there's no need to switch between VS Code and a separate terminal window.

---

### Step 2: Initialise the PowerGenome submodule

Switch uses PowerGenome as a **submodule** — a separate git repository nested inside the main one. When you clone the main repo, git doesn't automatically download the submodule contents. Without this step the `PowerGenome/` folder will be empty:

```
git submodule update --init
```

---

### Step 3: Check out the EDF baseline branch

When you first clone the repo you'll be on `main`, which is a clean copy of the original upstream Switch repo with no EDF modifications. All EDF work lives on `edf-baseline`, so switch to that:

```
git checkout edf-baseline
```

Your local files will now reflect the EDF baseline version of the code.

---

### Step 4: Create your own branch

You shouldn't work directly on `edf-baseline` — it's the shared team baseline and changes to it should only come through reviewed pull requests. Instead, create your own personal branch off `edf-baseline`. This gives you your own working copy to make changes in without affecting anyone else.

The `git checkout -b` command creates a new branch and switches to it in one step:

```
git checkout -b [yourname]/edf-baseline
```

For example:
```
git checkout -b peter/edf-baseline
```

Your branch starts as an identical copy of `edf-baseline`. Any changes you make will only affect your branch until you explicitly share them.

---

### Step 5: Set up your conda environment

The repository includes an `environment.yml` file that specifies all the Python packages needed to run Switch. Create or update your conda environment from this file - but your existing switch-peg-reed environment should work fine:

**If creating fresh:**
```
conda env create -f environment.yml
conda activate switch-pg-reeds
```

**If you already have the environment from a previous Switch installation:**
```
conda env update -f environment.yml
conda activate switch-pg-reeds
```

---

### Step 6: Get large data files

Some large files are excluded from git due to GitHub's 100MB file size limit. You'll need to obtain these separately:

- Copy `load_adjustments_caelp.csv` from `D:\Large Data Files` (on the VM) into `pg/extra_inputs/load_adjustments_caelp.csv` in your local repo

Alternatively you can regenerate it by running:
```
python make_study_loads.py
```

Git is configured to ignore this file so it will never be accidentally committed.

---

### Step 7: Configure your git identity

Git needs to know who you are in order to record your name on commits. If you haven't used git on this machine before, run:

```
git config --global user.email "your.email@edf.org"
git config --global user.name "Your Name"
```

---

You're now set up. Your local repo is on your own branch, based off the shared EDF baseline, and ready to work in.

---

## Part 2: Migrating your existing local changes

If you have been working from a local copy of Switch-USA-PG-ReEDS and have made your own modifications, this section explains how to bring those changes across into the EDF fork using git's merge functionality. This is preferable to manually copying files because git automatically identifies what has changed and only asks you to make decisions where there are genuine conflicts.

The process works in two stages:
1. **Align to `main`** — reconcile your changes with the upstream Switch baseline
2. **Align to `edf-baseline`** — reconcile your changes with the EDF modifications on top of that

---

### Before you start: check your old clone is a git repo

This process only works if your old local copy is a proper git clone rather than just a folder copy. Navigate to it in a terminal and run:

```
git status
```

If you see output listing modified and untracked files, you're good to proceed. If you get an error like `not a git repository`, your old copy is a folder copy — contact Tom for help migrating in that case.

Also make sure you have no uncommitted changes you want to keep. Stash them first:

```
git stash push -m "my local modifications"
```

---

### Stage 1: Align to main

This stage reconciles your local changes with the upstream Switch baseline — the same base that `main` on the EDF fork is built from.

**Step 1: Add your old clone as a remote in the EDF fork**

Navigate to your new EDF fork clone (from Part 1) and add your old local clone as a temporary remote. This lets git see both sets of changes and compare them directly:

```
cd Switch-USA-EDF
git remote add old-local /path/to/your/old/Switch-USA-PG-ReEDS
git fetch old-local
```

Replace `/path/to/your/old/Switch-USA-PG-ReEDS` with the actual path to your old clone, e.g. `D:\SWITCH\Switch-USA-PG-ReEDS`.

**Step 2: Check out main and merge your old changes in**

```
git checkout main
git merge old-local/main
```

Git will automatically merge changes where there is no overlap. Where both your old clone and the upstream repo have modified the same part of a file, git will flag a **conflict** for you to resolve manually.

**Step 3: Resolve any conflicts in modified files**

For each conflicted file, git will insert conflict markers showing both versions:

```
<<<<<<< HEAD
  [version from main]
=======
  [your version]
>>>>>>> old-local/main
```

Open each conflicted file in VS Code — it will show a merge editor with buttons to **Accept Current**, **Accept Incoming**, or **Accept Both**. Work through each conflict, choosing the right version, then save the file and mark it resolved:

```
git add [filename]
```

**Step 4: Handle new files from your old clone**

As well as conflicts in modified files, your old clone may contain new files that git doesn't automatically bring across during a merge — these will appear as **untracked files** when you run `git status`. You need to decide what to do with each one:

Sort them into three buckets:

- **Commit to the repo** — scripts, modules, config files, or data files that represent genuine EDF work the team should share. Copy these into the right location in your EDF fork clone and stage them with `git add [filename]`.

- **Ignore locally** — personal run scripts (`.bat` files), scratch files, output files, or large data files (>100MB). Add these to `.gitignore` so git stops flagging them. Open `.gitignore` in VS Code and add a line for each file or pattern, e.g.:
  ```
  *.bat
  my_scratch_analysis.py
  pg/extra_inputs/my_large_file.csv
  ```

- **Leave for now** — if you're unsure, you can leave files as untracked temporarily. They won't be committed unless you explicitly `git add` them. You can always decide later.

A useful rule of thumb: if another team member would need the file to reproduce your results or run the model in your configuration, it should be committed. If it's something only you use or that can be regenerated, keep it local.

Once you've staged your resolved conflicts and any new files to commit, finalise with:

```
git commit -m "chore: merge personal changes into main baseline"
```

**Step 5: Check for PowerGenome changes**

If you have made modifications to files inside the `PowerGenome/` submodule (e.g. `PowerGenome/powergenome/generators.py`), these need to be handled separately as the submodule has its own git history.

Navigate into the PowerGenome directory and check its status:

```
cd PowerGenome
git status
git diff
```

If you have local modifications, follow the same stash → pull → pop process to reconcile them with the upstream PowerGenome:

```
git stash push -m "my PowerGenome modifications"
git pull origin master
git stash pop
```

Resolve any conflicts, then commit your changes to a branch within the submodule:

```
git checkout -b [yourname]/edf-modifications
git add [modified files]
git commit -m "feat: [description of PowerGenome changes]"
```

Then return to the main repo and update the submodule pointer:

```
cd ..
git add PowerGenome
```

This tells the main repo to track your new PowerGenome branch commit rather than the old one.

---

### Stage 2: Align to edf-baseline

Now that your changes are reconciled with `main`, bring them across to your personal branch which is based on `edf-baseline`. This second merge will flag any conflicts between your changes and the EDF modifications already in `edf-baseline`.

**Step 1: Switch to your personal branch**

```
git checkout [yourname]/edf-baseline
```

**Step 2: Merge your updated main into your branch**

```
git merge main
```

Again, git will auto-merge where possible and flag conflicts where your changes overlap with the EDF modifications in `edf-baseline`. Resolve any conflicts as in Stage 1.

**Step 3: Review what's been brought across**

Before committing, it's worth reviewing what has come in. Run:

```
git diff edf-baseline
```

This shows everything that is different between your branch and `edf-baseline`. Also run `git status` to check for any untracked files that have come across. Apply the same three-bucket approach as Stage 1 Step 4 — commit genuine EDF work, ignore personal config, leave anything uncertain for now.

In particular, check:
- `switch/modules.txt` and `switch/options.txt` — make sure your module and solver choices don't conflict with the EDF baseline versions
- `pg/settings/scenario_management.yml` — your scenario definitions should sit alongside the EDF ones, not replace them
- `pg/extra_inputs/scenario_inputs.csv` — your scenario rows should be added to the existing EDF rows, not overwrite them
- Any files in `switch/study_modules/` — new modules you've added should be committed; changes to existing modules need careful review against the EDF baseline versions

**Step 4: Commit and push**

Stage the files you want to include and commit:

```
git add [files]
git commit -m "feat: migrate [yourname] local modifications to EDF fork"
git push origin [yourname]/edf-baseline
```

**Step 5: Clean up the temporary remote**

Remove the `old-local` remote now that you're done with it:

```
git remote remove old-local
```

---

### Step 6: Open a pull request

Once your changes are pushed to GitHub and you're happy with them, open a **pull request** to share them with the team. A pull request is a proposal to merge your branch into `edf-baseline` — it gives Tom and other team members a chance to review the changes before they become part of the shared baseline.

Go to `https://github.com/TMBrindle/Switch-USA-EDF`, click **Pull requests → New pull request**, and set:
- **Base branch**: `edf-baseline`
- **Compare branch**: `[yourname]/edf-baseline`

Add a description of what you changed and tag Tom to review.

---

## Day-to-day workflow (once set up)

**Starting new work — always start from an up-to-date baseline:**
```
git checkout edf-baseline
git pull origin edf-baseline        <- download latest team changes from GitHub
git checkout [yourname]/edf-baseline
git rebase edf-baseline             <- bring your branch up to date with latest baseline
```

**Saving your work locally:**
```
git add [files]
git commit -m "description of what you changed"
```

**Sharing with the team:**
```
git push origin [yourname]/edf-baseline
```
Then open a pull request on GitHub as described in Step 5 above.

---

## Branch structure

```
upstream (switch-model/Switch-USA-PG-ReEDS)
    |
    | periodic updates (Tom only)
    v
main (TMBrindle/Switch-USA-EDF)      <- stable, tested releases only
    |
    v
edf-baseline                         <- shared EDF working branch
    |            |            |
    v            v            v
Tom/           Peter/       Ollie/        <- individual work branches
```

---

## Pulling upstream Switch updates

Periodically Matthias will release updates to the original repo. To integrate these into the EDF fork, run the following from your local clone:

```
git remote add upstream https://github.com/switch-model/Switch-USA-PG-ReEDS.git
git checkout edf-baseline
git fetch upstream
git merge upstream/main
```

Resolve any conflicts, then push to the fork:
```
git push origin edf-baseline
```

Team members will pick up the updates next time they run `git pull origin edf-baseline`.

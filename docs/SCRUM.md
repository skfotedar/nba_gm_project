# Scrum working agreement — nba_gm_project

Solo Scrum, tuned for ~1-hour sessions. Claude acts as scrum master; the
tracker is `tools/scrum.py`, and the data is `scrum/board.json` (committed).

## Cadence

- **Sprint length:** 2 weeks, Monday to Sunday.
- **Release:** every sprint ends with a tagged pre-release, `v0.<sprint>.0`.
  The `0.` prefix is the signal that nothing is production-ready.
- **Sprints never extend.** If work isn't done, it carries over. The release
  ships whatever reached Done, even if the goal was missed. The cadence is the
  point; completeness is not.

## Units

- **1 story point ≈ one focused 1-hour session.**
- Stories bigger than 3 points get split before they enter a sprint.
- **Rhythm: 5 sessions/week, so 10 sessions per 2-week sprint.**
- **Capacity: commit 8, not 10.** The two spare sessions absorb a bad week,
  a story that runs long, or a mid-sprint addition, without breaking the
  release cadence. Raise or lower it once `velocity` has real data —
  after three sprints, use the velocity average instead of this rule.

## Ceremonies (fitted to 1-hour sessions)

| When | Ceremony | Time | Command |
|---|---|---|---|
| First session of sprint | Planning | 15 min | `sprint plan`, `sprint start` |
| Session 5 of 10 | Mid-sprint check | 5 min | `burndown` |
| Start of every session | Standup | 2 min | `standup` |
| Session 6 of 10 | Refinement | 10 min | `story add`, `story edit`, `story rank` |
| Last session of sprint | Review + retro + release | 30 min | `sprint close`, `release` |

For planning and review, run `report` and paste the output into the Claude
project chat — that's the scrum master check-in.

## Working agreements

1. WIP limit of 1: finish a story before starting another.
2. Every commit message starts with the story ID: `S-006: add apron tests`.
3. Mid-sprint scope changes go through `sprint add` so the burndown stays honest.
4. A story isn't Done until it meets the Definition of Done.
5. Retro action items are reviewed at the next planning (the tool shows them).
6. Ceremonies come out of the 10 sessions, not on top of them. Planning and
   review each eat roughly half a session; that is already priced into the
   capacity of 8.

## Definition of Done

- Acceptance criteria met
- pytest passes locally
- Architecture purity test passes (once it exists)
- Committed with the story ID in the message
- Pushed to GitHub

## Workflow states

`backlog` (product backlog, ordered) → `todo` (in sprint) → `doing` → `done`

## Command cheat sheet

```
python tools/scrum.py status                 # where am I?
python tools/scrum.py standup                # session check-in
python tools/scrum.py board                  # sprint kanban
python tools/scrum.py story move 6 doing     # S-006 -> doing
python tools/scrum.py story add              # interactive
python tools/scrum.py story show 6
python tools/scrum.py story edit 6 --points 2 --add-ac "Handles apron edge case"
python tools/scrum.py story note 6 "Salary matching differs above 2nd apron"
python tools/scrum.py story rank 13 1        # move S-013 to top of backlog
python tools/scrum.py story list [--all] [--epic E2] [--sprint current]
python tools/scrum.py epic list
python tools/scrum.py sprint plan | start | add 6 7 | remove 7 | close | list | show
python tools/scrum.py burndown
python tools/scrum.py velocity
python tools/scrum.py retro --show
python tools/scrum.py release [--dry-run]
python tools/scrum.py report                 # paste to Claude
```

`release` never runs git for you. It writes `docs/releases/v0.N.0.md` and
prints the `git add / commit / tag / push` commands to run in Git Bash.

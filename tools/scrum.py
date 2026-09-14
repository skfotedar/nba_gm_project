#!/usr/bin/env python
"""
scrum.py - a lightweight, file-based Scrum tracker for a solo project.

Data lives in scrum/board.json, which is committed to git, so every change
to the backlog and every sprint is versioned alongside the code.

Stdlib only. Deliberately lives in tools/, outside the product package, so
process tooling never leaks into the deterministic core.

Point scale: 1 story point ~= one focused ~1-hour session.

Common commands (run from the project root):
    python tools/scrum.py status               where am I?
    python tools/scrum.py standup              start-of-session check-in
    python tools/scrum.py board                sprint kanban
    python tools/scrum.py story move 4 doing   S-004 -> doing
    python tools/scrum.py story add            interactive
    python tools/scrum.py story list           product backlog
    python tools/scrum.py sprint plan          interactive planning
    python tools/scrum.py sprint start
    python tools/scrum.py burndown
    python tools/scrum.py sprint close         review + retro
    python tools/scrum.py release              release notes + git tag commands
    python tools/scrum.py report               markdown summary to paste to Claude
    python tools/scrum.py install-pycharm      write PyCharm run configurations

Add -h to any command for its options.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

try:  # keep Windows consoles from choking on non-ASCII titles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "scrum" / "board.json"
RELEASE_DIR = ROOT / "docs" / "releases"
RUN_DIR = ROOT / ".run"

STATUSES = ["backlog", "todo", "doing", "done"]
MAX_POINTS_BEFORE_SPLIT = 3

DEFAULT_DOD = [
    "Acceptance criteria met",
    "pytest passes locally",
    "Architecture purity test passes (once it exists)",
    "Committed with the story ID in the message, e.g. 'S-004: ...'",
    "Pushed to GitHub",
]


# --------------------------------------------------------------------------
# storage & small helpers
# --------------------------------------------------------------------------
def today() -> date:
    return date.today()


def iso(d: date) -> str:
    return d.isoformat()


def parse_date(s: str) -> date:
    return date.fromisoformat(s)


def next_monday(d: date | None = None) -> date:
    d = d or today()
    return d + timedelta(days=((7 - d.weekday()) % 7) or 7)


def empty_board(project: str) -> dict:
    return {
        "meta": {
            "project": project,
            "schema": 1,
            "next_story": 1,
            "sprint_length_days": 14,
            "default_capacity": 6,
            "definition_of_done": list(DEFAULT_DOD),
        },
        "epics": [],
        "stories": [],
        "sprints": [],
        "standups": [],
    }


def load() -> dict:
    if not BOARD.exists():
        sys.exit(f"No board at {BOARD}.\nRun: python tools/scrum.py init")
    with BOARD.open(encoding="utf-8") as f:
        return json.load(f)


def save(data: dict) -> None:
    BOARD.parent.mkdir(parents=True, exist_ok=True)
    tmp = BOARD.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(BOARD)


def ask(prompt: str, default=None, required: bool = True) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            val = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            if default is None and required:
                sys.exit("\nNo input available - pass the value as an argument instead.")
            val = ""
        if not val and default is not None:
            return str(default)
        if val or not required:
            return val
        print("  (required)")


def ask_int(prompt: str, default=None) -> int:
    while True:
        raw = ask(prompt, default=default)
        try:
            return int(raw)
        except ValueError:
            print("  (enter a whole number)")


def ask_list(prompt: str) -> list[str]:
    print(f"{prompt} (one per line, blank line to finish)")
    items = []
    while True:
        try:
            line = input("  - ").strip()
        except EOFError:
            break
        if not line:
            break
        items.append(line)
    return items


def confirm(prompt: str) -> bool:
    return ask(f"{prompt} [y/N]", default="").lower().startswith("y")


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def pts(stories) -> int:
    return sum(int(s.get("points") or 0) for s in stories)


def pts_str(s) -> str:
    return "?" if s.get("points") is None else str(s["points"])


# --------------------------------------------------------------------------
# lookups
# --------------------------------------------------------------------------
def norm_story_id(raw: str) -> str:
    sid = raw.strip().upper()
    if sid.startswith("S-"):
        sid = sid[2:]
    elif sid.startswith("S"):
        sid = sid[1:]
    return f"S-{int(sid):03d}" if sid.isdigit() else raw.strip().upper()


def find_story(data: dict, raw: str) -> dict:
    sid = norm_story_id(raw)
    for s in data["stories"]:
        if s["id"] == sid:
            return s
    sys.exit(f"Story {sid} not found.")


def find_epic(data: dict, eid: str) -> dict | None:
    for e in data["epics"]:
        if e["id"] == eid.upper():
            return e
    return None


def sprints_with(data, status):
    return [s for s in data["sprints"] if s["status"] == status]


def active_sprint(data):
    a = sprints_with(data, "active")
    return a[0] if a else None


def planned_sprint(data):
    p = sprints_with(data, "planned")
    return p[0] if p else None


def get_sprint(data, n: int) -> dict:
    for s in data["sprints"]:
        if s["id"] == n:
            return s
    sys.exit(f"Sprint {n} not found.")


def pick_sprint(data, n=None) -> dict:
    if n:
        return get_sprint(data, int(n))
    sp = active_sprint(data) or planned_sprint(data)
    if sp:
        return sp
    closed = sprints_with(data, "closed")
    if closed:
        return closed[-1]
    sys.exit("No sprints yet. Run: python tools/scrum.py sprint plan")


def sprint_stories(data, sp) -> list[dict]:
    return [s for s in data["stories"] if s.get("sprint") == sp["id"]]


def backlog(data) -> list[dict]:
    return [s for s in data["stories"] if s["status"] == "backlog"]


def recent_velocity(data, n=3):
    closed = [s for s in sprints_with(data, "closed") if s.get("completed_points") is not None]
    if not closed:
        return None
    last = closed[-n:]
    return round(sum(s["completed_points"] for s in last) / len(last), 1)


# --------------------------------------------------------------------------
# printing
# --------------------------------------------------------------------------
def print_stories(stories) -> None:
    if not stories:
        print("  (none)")
        return
    print(f"  {'ID':<6} {'PTS':>3}  {'STATUS':<7} {'EPIC':<4} {'SPR':>3}  TITLE")
    for s in stories:
        spr = s.get("sprint") or "-"
        print(
            f"  {s['id']:<6} {pts_str(s):>3}  {s['status']:<7} "
            f"{(s.get('epic') or '-'):<4} {str(spr):>3}  {trunc(s['title'], 60)}"
        )


def sprint_timing(sp) -> str:
    start, end, t = parse_date(sp["start"]), parse_date(sp["end"]), today()
    ndays = (end - start).days + 1
    if sp["status"] == "planned":
        return f"planned, starts {sp['start']}"
    if sp["status"] == "closed":
        return f"closed {sp.get('closed')}"
    left = (end - t).days
    if left < 0:
        return f"OVERDUE by {-left} day(s) - time for review and close"
    day = max(1, min((t - start).days + 1, ndays))
    return f"day {day} of {ndays}, {left} day(s) left"


def print_sprint_header(data, sp) -> None:
    stories = sprint_stories(data, sp)
    done = pts([s for s in stories if s["status"] == "done"])
    print(f"{sp['name']} [{sp['status']}]  {sp['start']} -> {sp['end']}  ({sprint_timing(sp)})")
    print(f"Goal:    {sp['goal']}")
    print(f"Points:  {done}/{pts(stories)} done   capacity {sp.get('capacity')}   release {sp['release']}")


# --------------------------------------------------------------------------
# commands: init / epics
# --------------------------------------------------------------------------
def cmd_init(args):
    if BOARD.exists() and not args.force:
        sys.exit(f"{BOARD} already exists (use --force to overwrite).")
    save(empty_board(args.project or ROOT.name))
    print(f"Created {BOARD.relative_to(ROOT)}")


def cmd_epic_add(args):
    data = load()
    title = args.title or ask("Epic title")
    if args.id:
        eid = args.id.upper()
    else:
        nums = [int(e["id"][1:]) for e in data["epics"] if e["id"][1:].isdigit()]
        eid = f"E{max(nums, default=0) + 1}"
    if find_epic(data, eid):
        sys.exit(f"Epic {eid} already exists.")
    data["epics"].append({"id": eid, "title": title, "deferred": bool(args.deferred)})
    save(data)
    print(f"Added epic {eid}: {title}")


def cmd_epic_list(args):
    data = load()
    for e in data["epics"]:
        stories = [s for s in data["stories"] if s.get("epic") == e["id"]]
        done = [s for s in stories if s["status"] == "done"]
        flag = "  (deferred)" if e.get("deferred") else ""
        print(f"  {e['id']:<4} {pts(done):>3}/{pts(stories):<3} pts  {e['title']}{flag}")


# --------------------------------------------------------------------------
# commands: stories
# --------------------------------------------------------------------------
def cmd_story_add(args):
    data = load()
    interactive = args.title is None
    title = args.title or ask("Story title")
    epic = args.epic
    if epic is None and interactive:
        if data["epics"]:
            print("Epics: " + ", ".join(f"{e['id']}={e['title']}" for e in data["epics"]))
        epic = ask("Epic id", default="", required=False)
    epic = (epic or "").upper() or None
    if epic and not find_epic(data, epic):
        sys.exit(f"Epic {epic} does not exist. Add it with: epic add")
    points = args.points
    if points is None and interactive:
        raw = ask("Points (1 pt ~ one 1-hour session; blank = unestimated)", default="", required=False)
        points = int(raw) if raw.isdigit() else None
    desc = args.desc if args.desc is not None else (
        ask("Description (optional)", default="", required=False) if interactive else "")
    ac = args.ac or (ask_list("Acceptance criteria") if interactive else [])

    sid = f"S-{data['meta']['next_story']:03d}"
    data["meta"]["next_story"] += 1
    data["stories"].append({
        "id": sid, "title": title, "epic": epic, "points": points,
        "status": "backlog", "sprint": None, "description": desc,
        "acceptance": ac, "created": iso(today()), "started": None,
        "done": None, "notes": [],
    })
    save(data)
    print(f"Added {sid}: {title}")
    if points is not None and points > MAX_POINTS_BEFORE_SPLIT:
        print(f"  Scrum master: {points} pts is more than {MAX_POINTS_BEFORE_SPLIT} sessions. "
              "Consider splitting it so it fits comfortably in a sprint.")
    if not ac:
        print("  Scrum master: no acceptance criteria yet - add some before planning it into a sprint.")


def cmd_story_list(args):
    data = load()
    stories = data["stories"]
    if args.status:
        stories = [s for s in stories if s["status"] == args.status]
    elif not args.all:
        stories = [s for s in stories if s["status"] != "done"]
    if args.epic:
        stories = [s for s in stories if (s.get("epic") or "") == args.epic.upper()]
    if args.sprint:
        n = pick_sprint(data)["id"] if args.sprint == "current" else int(args.sprint)
        stories = [s for s in stories if s.get("sprint") == n]
    print_stories(stories)
    print(f"\n  {len(stories)} stories, {pts(stories)} pts "
          f"({sum(1 for s in stories if s.get('points') is None)} unestimated)")


def cmd_story_show(args):
    data = load()
    s = find_story(data, args.id)
    print(f"{s['id']}  {s['title']}")
    print(f"  status: {s['status']}   points: {pts_str(s)}   epic: {s.get('epic') or '-'}   "
          f"sprint: {s.get('sprint') or '-'}")
    print(f"  created {s['created']}   started {s.get('started') or '-'}   done {s.get('done') or '-'}")
    if s.get("description"):
        print(f"\n  {s['description']}")
    print("\n  Acceptance criteria:")
    for a in s.get("acceptance") or ["(none)"]:
        print(f"    [ ] {a}")
    if s.get("notes"):
        print("\n  Notes:")
        for n in s["notes"]:
            print(f"    {n['date']}  {n['text']}")


def cmd_story_move(args):
    data = load()
    act = active_sprint(data) or planned_sprint(data)
    if not args.id and act:
        print_sprint_header(data, act)
        print_stories([s for s in sprint_stories(data, act) if s["status"] != "done"])
        print()
    s = find_story(data, args.id or ask("Story id (e.g. S-004 or 4)"))
    status = (args.status or ask("New status (todo / doing / done / backlog)")).lower()
    if status not in STATUSES:
        sys.exit(f"Status must be one of {STATUSES}")

    live = active_sprint(data)
    if status in ("doing", "done") and (not live or s.get("sprint") != live["id"]):
        print("  Scrum master: this story isn't in the active sprint. That's scope creep -")
        print("  fine occasionally, but add it deliberately with 'sprint add' so the burndown stays honest.")

    old = s["status"]
    s["status"] = status
    if status == "backlog":
        s["sprint"] = None
    if status == "doing" and not s.get("started"):
        s["started"] = iso(today())
    if status == "done":
        s["done"] = iso(today())
    elif old == "done":
        s["done"] = None
    save(data)
    print(f"{s['id']}: {old} -> {status}")

    if status == "done":
        print("\nDefinition of Done:")
        for item in data["meta"]["definition_of_done"]:
            print(f"  [ ] {item}")
        title = s["title"].replace('"', "'")
        print(f'\nSuggested commit:  git commit -m "{s["id"]}: {title}"')
    elif status == "doing":
        in_progress = [x for x in data["stories"] if x["status"] == "doing"]
        if len(in_progress) > 1:
            print(f"  Scrum master: {len(in_progress)} stories in progress. "
                  "Solo WIP limit of 1 keeps sessions focused - finish before starting.")


def cmd_story_edit(args):
    data = load()
    s = find_story(data, args.id)
    if args.title:
        s["title"] = args.title
    if args.points is not None:
        s["points"] = args.points
    if args.epic is not None:
        e = args.epic.upper() or None
        if e and not find_epic(data, e):
            sys.exit(f"Epic {e} does not exist.")
        s["epic"] = e
    if args.desc is not None:
        s["description"] = args.desc
    for a in args.add_ac or []:
        s.setdefault("acceptance", []).append(a)
    save(data)
    print(f"Updated {s['id']}")


def cmd_story_note(args):
    data = load()
    s = find_story(data, args.id)
    text = args.text or ask("Note")
    s.setdefault("notes", []).append({"date": iso(today()), "text": text})
    save(data)
    print(f"Noted on {s['id']}")


def cmd_story_rank(args):
    """Reorder the product backlog: position 1 = highest priority."""
    data = load()
    s = find_story(data, args.id)
    if s["status"] != "backlog":
        sys.exit("Only product-backlog stories can be ranked.")
    data["stories"].remove(s)
    bl = backlog(data)
    pos = max(1, args.position)
    if pos > len(bl):
        data["stories"].append(s)
    else:
        data["stories"].insert(data["stories"].index(bl[pos - 1]), s)
    save(data)
    print(f"{s['id']} is now #{min(pos, len(bl) + 1)} in the product backlog")


# --------------------------------------------------------------------------
# commands: sprints
# --------------------------------------------------------------------------
def add_to_sprint(data, sp, ids) -> None:
    for raw in ids:
        s = find_story(data, raw)
        if s["status"] == "done":
            print(f"  {s['id']} is already done - skipped")
            continue
        s["sprint"] = sp["id"]
        if s["status"] == "backlog":
            s["status"] = "todo"
        if s.get("points") is None:
            print(f"  {s['id']} has no estimate - estimate it before starting the sprint")
        print(f"  + {s['id']} ({pts_str(s)}) {trunc(s['title'], 55)}")
    total = pts(sprint_stories(data, sp))
    cap = sp.get("capacity") or 0
    print(f"  Sprint {sp['id']}: {total} pts committed vs capacity {cap}")
    if cap and total > cap:
        print("  Scrum master: over capacity. Overcommitting is the #1 way to lose the cadence - trim.")


def cmd_sprint_plan(args):
    data = load()
    if planned_sprint(data):
        sys.exit(f"{planned_sprint(data)['name']} is already planned. "
                 "Use 'sprint add', 'sprint start', or 'sprint cancel'.")
    n = max((s["id"] for s in data["sprints"]), default=0) + 1
    act = active_sprint(data)
    default_start = parse_date(act["end"]) + timedelta(days=1) if act else next_monday()

    closed = sprints_with(data, "closed")
    if closed and closed[-1].get("retro") and closed[-1]["retro"].get("actions"):
        print(f"Retro actions from {closed[-1]['name']}:")
        for a in closed[-1]["retro"]["actions"]:
            print(f"  * {a}")
        print()

    vel = recent_velocity(data)
    suggested = int(round(vel)) if vel else data["meta"]["default_capacity"]
    print(f"Velocity (avg last 3 sprints): {vel if vel is not None else 'n/a yet'}")

    interactive = args.goal is None
    goal = args.goal or ask("Sprint goal (what is demonstrably true at the end?)")
    if args.start:
        start = parse_date(args.start)
    elif interactive:
        start = parse_date(ask("Start date YYYY-MM-DD", default=iso(default_start)))
    else:
        start = default_start
    length = args.length or data["meta"]["sprint_length_days"]
    if args.capacity is not None:
        capacity = args.capacity
    elif interactive:
        capacity = ask_int("Capacity in points (= 1-hour sessions you can realistically do)", default=suggested)
    else:
        capacity = suggested
    end = start + timedelta(days=length - 1)
    sp = {
        "id": n, "name": f"Sprint {n}", "goal": goal,
        "start": iso(start), "end": iso(end), "capacity": capacity,
        "status": "planned", "committed_points": None, "completed_points": None,
        "release": f"v0.{n}.0", "goal_met": None, "carried_over": [],
        "retro": None, "closed": None, "released": None,
    }
    data["sprints"].append(sp)
    print(f"\nPlanned {sp['name']}: {sp['start']} -> {sp['end']}, release {sp['release']}")

    ids = args.stories
    if not ids and interactive:
        print("\nTop of product backlog:")
        print_stories(backlog(data)[:12])
        raw = ask("\nStory ids to pull in (space separated, blank to skip)", default="", required=False)
        ids = raw.split()
    if ids:
        add_to_sprint(data, sp, ids)
    save(data)
    print("\nWhen ready:  python tools/scrum.py sprint start")


def cmd_sprint_add(args):
    data = load()
    sp = planned_sprint(data) or active_sprint(data)
    if not sp:
        sys.exit("No planned or active sprint. Run: sprint plan")
    if sp["status"] == "active":
        print("  Scrum master: adding to an active sprint changes the commitment. "
              "Consider swapping something out.")
    add_to_sprint(data, sp, args.ids)
    save(data)


def cmd_sprint_remove(args):
    data = load()
    for raw in args.ids:
        s = find_story(data, raw)
        s["sprint"] = None
        s["status"] = "backlog"
        print(f"  - {s['id']} back to product backlog")
    save(data)


def cmd_sprint_start(args):
    data = load()
    if active_sprint(data):
        sys.exit(f"{active_sprint(data)['name']} is still active. Close it first.")
    sp = planned_sprint(data)
    if not sp:
        sys.exit("Nothing planned. Run: sprint plan")
    stories = sprint_stories(data, sp)
    if not stories:
        sys.exit("Sprint has no stories. Use: sprint add S-001 S-002 ...")
    unest = [s["id"] for s in stories if s.get("points") is None]
    if unest:
        sys.exit(f"Estimate these first: {', '.join(unest)}  (story edit ID --points N)")
    start, end = parse_date(sp["start"]), parse_date(sp["end"])
    if today() != start and not args.keep_dates:
        length = (end - start).days + 1
        sp["start"], sp["end"] = iso(today()), iso(today() + timedelta(days=length - 1))
        print(f"Dates reset to today: {sp['start']} -> {sp['end']} (use --keep-dates to avoid)")
    sp["status"] = "active"
    sp["committed_points"] = pts(stories)
    save(data)
    print_sprint_header(data, sp)
    print("\nFirst session: python tools/scrum.py standup, then move a story to doing.")


def cmd_sprint_cancel(args):
    data = load()
    sp = planned_sprint(data)
    if not sp:
        sys.exit("Only a planned (not started) sprint can be cancelled.")
    for s in sprint_stories(data, sp):
        s["sprint"], s["status"] = None, "backlog"
    data["sprints"].remove(sp)
    save(data)
    print(f"Cancelled {sp['name']}; its stories are back in the product backlog.")


def collect_retro() -> dict:
    print("\n--- Retrospective ---")
    return {
        "went_well": ask_list("What went well?"),
        "improve": ask_list("What should change?"),
        "actions": ask_list("Action items for next sprint (small and concrete)"),
    }


def cmd_sprint_close(args):
    data = load()
    sp = active_sprint(data)
    if not sp:
        sys.exit("No active sprint.")
    stories = sprint_stories(data, sp)
    done = [s for s in stories if s["status"] == "done"]
    unfinished = [s for s in stories if s["status"] != "done"]

    print("--- Sprint review ---")
    print_sprint_header(data, sp)
    print("\nDone:")
    print_stories(done)
    if unfinished:
        print("\nNot done (will return to the product backlog):")
        print_stories(unfinished)
        if not args.yes and not confirm("Continue closing?"):
            sys.exit("Not closed.")

    sp["goal_met"] = args.goal_met if args.goal_met is not None else confirm("Was the sprint goal met?")
    for s in unfinished:
        s["sprint"], s["status"] = None, "backlog"
        s["carried"] = s.get("carried", 0) + 1
    sp["carried_over"] = [s["id"] for s in unfinished]
    sp["completed_points"] = pts(done)
    sp["status"] = "closed"
    sp["closed"] = iso(today())
    if not args.no_retro:
        sp["retro"] = collect_retro()
    save(data)

    print(f"\nClosed {sp['name']}: {sp['completed_points']}/{sp['committed_points']} pts, "
          f"goal {'met' if sp['goal_met'] else 'not met'}.")
    print(f"Velocity (avg last 3): {recent_velocity(data)}")
    print("Next:  python tools/scrum.py release   then   python tools/scrum.py sprint plan")


def cmd_sprint_list(args):
    data = load()
    if not data["sprints"]:
        print("  (no sprints yet)")
    for sp in data["sprints"]:
        committed = sp.get("committed_points")
        completed = sp.get("completed_points")
        score = f"{completed}/{committed}" if completed is not None else f"-/{committed or pts(sprint_stories(data, sp))}"
        goal = {True: "met", False: "missed", None: ""}[sp.get("goal_met")]
        rel = sp["release"] + (" (released)" if sp.get("released") else "")
        print(f"  {sp['name']:<10} {sp['status']:<8} {sp['start']} -> {sp['end']}  "
              f"{score:>7} pts  {goal:<6}  {rel}")
        print(f"             {trunc(sp['goal'], 70)}")


def cmd_sprint_show(args):
    data = load()
    sp = pick_sprint(data, args.n)
    print_sprint_header(data, sp)
    print()
    print_stories(sprint_stories(data, sp))
    if sp.get("carried_over"):
        print(f"\n  Carried over: {', '.join(sp['carried_over'])}")


def cmd_velocity(args):
    data = load()
    closed = sprints_with(data, "closed")
    if not closed:
        print("No closed sprints yet.")
        return
    for sp in closed:
        c = sp.get("completed_points") or 0
        bar = "#" * c
        print(f"  {sp['name']:<10} {c:>3}/{sp.get('committed_points') or 0:<3} {bar}")
    print(f"\n  Average of last 3: {recent_velocity(data)}  <- use this as next capacity")


# --------------------------------------------------------------------------
# commands: board / status / burndown / standup / retro
# --------------------------------------------------------------------------
def cmd_board(args):
    data = load()
    sp = pick_sprint(data, args.sprint)
    stories = sprint_stories(data, sp)
    print_sprint_header(data, sp)
    print()
    cols = {"todo": [], "doing": [], "done": []}
    for s in stories:
        cols.get(s["status"], cols["todo"]).append(s)
    w = args.width
    print(" | ".join(f"{k.upper()} ({pts(v)} pt)".ljust(w) for k, v in cols.items()))
    print("-+-".join("-" * w for _ in cols))
    for i in range(max((len(v) for v in cols.values()), default=0)):
        cells = []
        for v in cols.values():
            text = f"{v[i]['id']} ({pts_str(v[i])}) {v[i]['title']}" if i < len(v) else ""
            cells.append(trunc(text, w).ljust(w))
        print(" | ".join(cells))


def cmd_status(args):
    data = load()
    act, pl = active_sprint(data), planned_sprint(data)
    if act:
        print_sprint_header(data, act)
        doing = [s for s in sprint_stories(data, act) if s["status"] == "doing"]
        todo = [s for s in sprint_stories(data, act) if s["status"] == "todo"]
        print(f"\nIn progress: " + (", ".join(f"{s['id']} {trunc(s['title'], 40)}" for s in doing) or "nothing"))
        if not doing and todo:
            print(f"Next up:     {todo[0]['id']} {todo[0]['title']}")
        left = (parse_date(act["end"]) - today()).days
        if left <= 1:
            print("\nScrum master: sprint ends now. Review -> 'sprint close' -> 'release'.")
    else:
        print("No active sprint.")
    if pl:
        print(f"\n{pl['name']} planned for {pl['start']}: {pts(sprint_stories(data, pl))} pts. "
              "Start with: sprint start")
    bl = backlog(data)
    print(f"\nProduct backlog: {len(bl)} stories, {pts(bl)} pts "
          f"({sum(1 for s in bl if s.get('points') is None)} unestimated)")
    if data["standups"]:
        print(f"Last standup: {data['standups'][-1]['date']}")
    unreleased = [s for s in sprints_with(data, "closed") if not s.get("released")]
    if unreleased:
        print(f"\nScrum master: {unreleased[-1]['name']} closed but not released. Run: release")
    if not act and not pl:
        print("\nScrum master: no sprint in flight. Run: sprint plan")


def burndown_series(data, sp):
    stories = sprint_stories(data, sp)
    carried = [s for s in data["stories"] if s["id"] in (sp.get("carried_over") or [])]
    total = pts(stories) + pts(carried)
    start, end = parse_date(sp["start"]), parse_date(sp["end"])
    ndays = (end - start).days + 1
    last = end if sp["status"] == "closed" else min(today(), end)
    actual, ideal = [], []
    for i in range(ndays):
        d = start + timedelta(days=i)
        ideal.append(total - total * i / (ndays - 1) if ndays > 1 else 0)
        if d > last:
            actual.append(None)
            continue
        burned = sum(int(s.get("points") or 0) for s in stories
                     if s.get("done") and parse_date(s["done"]) <= d)
        actual.append(total - burned)
    return total, start, actual, ideal


def cmd_burndown(args):
    data = load()
    sp = pick_sprint(data, args.sprint)
    if sp["status"] == "planned":
        sys.exit("Sprint hasn't started yet.")
    total, start, actual, ideal = burndown_series(data, sp)
    print_sprint_header(data, sp)
    print("\n  * actual   . ideal   o both\n")
    height = max(total, 1)
    rows = min(height, 12)

    def row_of(v):
        return None if v is None else round(v / height * rows)

    for r in range(rows, -1, -1):
        line = []
        for a, i in zip(actual, ideal):
            ra, ri = row_of(a), row_of(i)
            line.append("o" if ra == r and ri == r else "*" if ra == r else "." if ri == r else " ")
        print(f"  {height * r / rows:5.1f} | " + " ".join(line))
    print("        +" + "--" * len(actual))
    print("  day    " + " ".join(str((k + 1) % 10) for k in range(len(actual))))
    today_idx = next((k for k, a in enumerate(actual) if a is None), len(actual)) - 1
    if today_idx >= 0:
        gap = actual[today_idx] - ideal[today_idx]
        state = "on track" if gap <= 0.5 else f"{gap:.1f} pts behind ideal"
        print(f"\n  Remaining: {actual[today_idx]} pts ({state})")


def cmd_standup(args):
    data = load()
    if args.show:
        for s in data["standups"][-args.last:]:
            print(f"{s['time']}  (sprint {s.get('sprint') or '-'})")
            for k, label in (("last", "Last"), ("today", "This"), ("blockers", "Blockers")):
                if s.get(k):
                    print(f"  {label:<9}{s[k]}")
        return
    act = active_sprint(data)
    if act:
        print_sprint_header(data, act)
        doing = [s for s in sprint_stories(data, act) if s["status"] == "doing"]
        if doing:
            print("In progress: " + ", ".join(f"{s['id']} {trunc(s['title'], 40)}" for s in doing))
        print()
    last = args.last_session if args.last_session is not None else ask(
        "Last session I finished", default="", required=False)
    now = args.today if args.today is not None else ask(
        "This session I'll do", default="", required=False)
    blockers = args.blockers if args.blockers is not None else ask(
        "Blockers", default="", required=False)
    data["standups"].append({
        "date": iso(today()), "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sprint": act["id"] if act else None,
        "last": last, "today": now, "blockers": blockers,
    })
    save(data)
    print("Logged.")
    if blockers:
        print("Scrum master: blocker logged - bring it to Claude, or make it a story.")


def cmd_retro(args):
    data = load()
    sp = get_sprint(data, int(args.n)) if args.n else (sprints_with(data, "closed") or [None])[-1]
    if not sp:
        sys.exit("No closed sprint to retro.")
    if args.show:
        r = sp.get("retro") or {}
        for k, label in (("went_well", "Went well"), ("improve", "Change"), ("actions", "Actions")):
            print(f"{label}:")
            for item in r.get(k) or ["(none)"]:
                print(f"  * {item}")
        return
    new = collect_retro()
    r = sp.get("retro") or {"went_well": [], "improve": [], "actions": []}
    for k in new:
        r.setdefault(k, []).extend(new[k])
    sp["retro"] = r
    save(data)
    print(f"Retro saved for {sp['name']}")


# --------------------------------------------------------------------------
# commands: release / report / pycharm
# --------------------------------------------------------------------------
def release_notes(data, sp, version) -> str:
    done = [s for s in sprint_stories(data, sp) if s["status"] == "done"]
    carried = [s for s in data["stories"] if s["id"] in (sp.get("carried_over") or [])]
    epics = {e["id"]: e["title"] for e in data["epics"]}
    out = [
        f"# {version} - {sp['name']}",
        "",
        f"Released {iso(today())}. Pre-release cadence build: not production-ready.",
        "",
        "## Sprint goal",
        "",
        f"{sp['goal']} ({'met' if sp.get('goal_met') else 'not met'})",
        "",
        f"## Delivered ({sp.get('completed_points', 0)} of {sp.get('committed_points', 0)} points)",
        "",
    ]
    by_epic: dict[str, list] = {}
    for s in done:
        by_epic.setdefault(s.get("epic") or "-", []).append(s)
    for eid, items in by_epic.items():
        out.append(f"### {eid} {epics.get(eid, '')}".rstrip())
        out.append("")
        out += [f"- {s['id']} {s['title']} ({pts_str(s)} pt)" for s in items]
        out.append("")
    if not done:
        out += ["Nothing reached Done this sprint.", ""]
    if carried:
        out += ["## Carried over", ""]
        out += [f"- {s['id']} {s['title']}" for s in carried]
        out.append("")
    actions = (sp.get("retro") or {}).get("actions") or []
    if actions:
        out += ["## Retro actions for next sprint", ""]
        out += [f"- {a}" for a in actions]
        out.append("")
    return "\n".join(out)


def cmd_release(args):
    data = load()
    if args.sprint:
        sp = get_sprint(data, int(args.sprint))
    else:
        pending = [s for s in sprints_with(data, "closed") if not s.get("released")]
        if not pending:
            if active_sprint(data):
                sys.exit("Close the active sprint first: sprint close")
            sys.exit("No closed, unreleased sprint.")
        sp = pending[-1]
    version = args.version or sp["release"]
    notes = release_notes(data, sp, version)
    if args.dry_run:
        print(notes)
        return
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    path = RELEASE_DIR / f"{version}.md"
    path.write_text(notes, encoding="utf-8", newline="\n")
    sp["released"] = iso(today())
    sp["release"] = version
    save(data)
    rel = path.relative_to(ROOT).as_posix()
    goal = sp["goal"].replace('"', "'")
    print(f"Wrote {rel}\n")
    print("Run these in Git Bash (review with git status / git diff first):\n")
    print(f"  git add scrum/board.json {rel}")
    print(f'  git commit -m "Release {version} ({sp["name"]}): {goal}"')
    print(f'  git tag -a {version} -m "{sp["name"]}: {goal}"')
    print("  git push --follow-tags")
    print("\nOptional, if you use the GitHub CLI:")
    print(f"  gh release create {version} --prerelease --title \"{version}\" --notes-file {rel}")


def cmd_report(args):
    """Markdown snapshot to paste into a chat with Claude (your scrum master)."""
    data = load()
    out = [f"## Scrum report - {data['meta']['project']} - {iso(today())}", ""]
    sp = active_sprint(data) or planned_sprint(data)
    if sp:
        stories = sprint_stories(data, sp)
        out += [
            f"**{sp['name']}** ({sp['status']}, {sprint_timing(sp)})",
            f"Goal: {sp['goal']}",
            f"Points: {pts([s for s in stories if s['status'] == 'done'])}/{pts(stories)} done, "
            f"capacity {sp.get('capacity')}",
            "",
        ]
        for st in ("doing", "todo", "done"):
            items = [s for s in stories if s["status"] == st]
            if items:
                out.append(f"{st.upper()}: " + "; ".join(f"{s['id']} {s['title']} ({pts_str(s)})" for s in items))
        if sp["status"] == "active":
            total, start, actual, ideal = burndown_series(data, sp)
            seen = [(i, a) for i, a in enumerate(actual) if a is not None]
            if seen:
                i, a = seen[-1]
                out.append(f"\nBurndown: {a} pts remaining on day {i + 1}; ideal {ideal[i]:.1f}")
    else:
        out.append("No sprint in flight.")
    vel = recent_velocity(data)
    out.append(f"\nVelocity (avg last 3): {vel if vel is not None else 'n/a'}")
    bl = backlog(data)
    out.append(f"Product backlog: {len(bl)} stories / {pts(bl)} pts. Top 5:")
    out += [f"- {s['id']} {s['title']} ({pts_str(s)})" for s in bl[:5]]
    ups = data["standups"][-args.standups:]
    if ups:
        out += ["", "Recent standups:"]
        for s in ups:
            out.append(f"- {s['time']}: last={s['last'] or '-'} | now={s['today'] or '-'} | "
                       f"blockers={s['blockers'] or 'none'}")
    closed = sprints_with(data, "closed")
    if closed and (closed[-1].get("retro") or {}).get("actions"):
        out += ["", f"Open retro actions ({closed[-1]['name']}):"]
        out += [f"- {a}" for a in closed[-1]["retro"]["actions"]]
    print("\n".join(out))


RUN_CONFIGS = [
    ("Status", "status"),
    ("Standup", "standup"),
    ("Board", "board"),
    ("Move story", "story move"),
    ("Add story", "story add"),
    ("Backlog", "story list"),
    ("Burndown", "burndown"),
    ("Plan sprint", "sprint plan"),
    ("Start sprint", "sprint start"),
    ("Close sprint + retro", "sprint close"),
    ("Release", "release"),
    ("Report for Claude", "report"),
]

RUN_XML = """<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="{name}" type="PythonConfigurationType" factoryName="Python" folderName="Scrum">
    <module name="{module}" />
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <option name="SDK_HOME" value="" />
    <option name="WORKING_DIRECTORY" value="$PROJECT_DIR$" />
    <option name="IS_MODULE_SDK" value="true" />
    <option name="ADD_CONTENT_ROOTS" value="true" />
    <option name="ADD_SOURCE_ROOTS" value="true" />
    <option name="SCRIPT_NAME" value="$PROJECT_DIR$/tools/scrum.py" />
    <option name="PARAMETERS" value="{params}" />
    <option name="SHOW_COMMAND_LINE" value="false" />
    <option name="EMULATE_TERMINAL" value="false" />
    <option name="MODULE_MODE" value="false" />
    <option name="REDIRECT_INPUT" value="false" />
    <option name="INPUT_FILE" value="" />
    <method v="2" />
  </configuration>
</component>
"""


def cmd_install_pycharm(args):
    RUN_DIR.mkdir(exist_ok=True)
    module = args.module or ROOT.name
    for name, params in RUN_CONFIGS:
        fname = "Scrum_" + "".join(c if c.isalnum() else "_" for c in name) + ".run.xml"
        (RUN_DIR / fname).write_text(
            RUN_XML.format(name=name, module=module, params=params), encoding="utf-8", newline="\n")
        print(f"  .run/{fname}")
    print(f"\nWrote {len(RUN_CONFIGS)} run configurations (module '{module}').")
    print("PyCharm picks them up automatically - look for the 'Scrum' folder in the run dropdown.")


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scrum", description="Solo Scrum tracker (1 pt ~ one 1-hour session).")
    sub = p.add_subparsers(dest="cmd")

    x = sub.add_parser("init", help="create scrum/board.json")
    x.add_argument("--project")
    x.add_argument("--force", action="store_true")
    x.set_defaults(func=cmd_init)

    sub.add_parser("status", help="where am I?").set_defaults(func=cmd_status)

    x = sub.add_parser("board", help="kanban for the current sprint")
    x.add_argument("--sprint", type=int)
    x.add_argument("--width", type=int, default=34)
    x.set_defaults(func=cmd_board)

    x = sub.add_parser("burndown", help="ASCII burndown chart")
    x.add_argument("--sprint", type=int)
    x.set_defaults(func=cmd_burndown)

    x = sub.add_parser("standup", help="start-of-session check-in")
    x.add_argument("-l", "--last-session")
    x.add_argument("-t", "--today")
    x.add_argument("-b", "--blockers")
    x.add_argument("--show", action="store_true")
    x.add_argument("--last", type=int, default=5)
    x.set_defaults(func=cmd_standup)

    sub.add_parser("velocity", help="velocity history").set_defaults(func=cmd_velocity)

    x = sub.add_parser("retro", help="add to or show a sprint retrospective")
    x.add_argument("n", nargs="?")
    x.add_argument("--show", action="store_true")
    x.set_defaults(func=cmd_retro)

    x = sub.add_parser("release", help="write release notes and print git tag commands")
    x.add_argument("--sprint", type=int)
    x.add_argument("--version")
    x.add_argument("--dry-run", action="store_true")
    x.set_defaults(func=cmd_release)

    x = sub.add_parser("report", help="markdown snapshot to paste to Claude")
    x.add_argument("--standups", type=int, default=5)
    x.set_defaults(func=cmd_report)

    x = sub.add_parser("install-pycharm", help="write .run/ configurations")
    x.add_argument("--module", help="PyCharm module name (default: project folder name)")
    x.set_defaults(func=cmd_install_pycharm)

    # epics
    e = sub.add_parser("epic", help="epics").add_subparsers(dest="sub", required=True)
    x = e.add_parser("add")
    x.add_argument("title", nargs="?")
    x.add_argument("--id")
    x.add_argument("--deferred", action="store_true")
    x.set_defaults(func=cmd_epic_add)
    e.add_parser("list").set_defaults(func=cmd_epic_list)

    # stories
    s = sub.add_parser("story", help="stories").add_subparsers(dest="sub", required=True)
    x = s.add_parser("add")
    x.add_argument("title", nargs="?")
    x.add_argument("--epic")
    x.add_argument("--points", type=int)
    x.add_argument("--desc")
    x.add_argument("--ac", action="append", help="acceptance criterion (repeatable)")
    x.set_defaults(func=cmd_story_add)
    x = s.add_parser("list")
    x.add_argument("--status", choices=STATUSES)
    x.add_argument("--epic")
    x.add_argument("--sprint", help="sprint number or 'current'")
    x.add_argument("--all", action="store_true", help="include done stories")
    x.set_defaults(func=cmd_story_list)
    x = s.add_parser("show")
    x.add_argument("id")
    x.set_defaults(func=cmd_story_show)
    x = s.add_parser("move")
    x.add_argument("id", nargs="?")
    x.add_argument("status", nargs="?")
    x.set_defaults(func=cmd_story_move)
    x = s.add_parser("edit")
    x.add_argument("id")
    x.add_argument("--title")
    x.add_argument("--points", type=int)
    x.add_argument("--epic")
    x.add_argument("--desc")
    x.add_argument("--add-ac", action="append")
    x.set_defaults(func=cmd_story_edit)
    x = s.add_parser("note")
    x.add_argument("id")
    x.add_argument("text", nargs="?")
    x.set_defaults(func=cmd_story_note)
    x = s.add_parser("rank", help="reorder product backlog (1 = top)")
    x.add_argument("id")
    x.add_argument("position", type=int)
    x.set_defaults(func=cmd_story_rank)

    # sprints
    sp = sub.add_parser("sprint", help="sprints").add_subparsers(dest="sub", required=True)
    x = sp.add_parser("plan")
    x.add_argument("--goal")
    x.add_argument("--start", help="YYYY-MM-DD")
    x.add_argument("--length", type=int, help="days (default from meta)")
    x.add_argument("--capacity", type=int)
    x.add_argument("--stories", nargs="*")
    x.set_defaults(func=cmd_sprint_plan)
    x = sp.add_parser("add")
    x.add_argument("ids", nargs="+")
    x.set_defaults(func=cmd_sprint_add)
    x = sp.add_parser("remove")
    x.add_argument("ids", nargs="+")
    x.set_defaults(func=cmd_sprint_remove)
    x = sp.add_parser("start")
    x.add_argument("--keep-dates", action="store_true")
    x.set_defaults(func=cmd_sprint_start)
    x = sp.add_parser("close")
    x.add_argument("--yes", action="store_true")
    x.add_argument("--no-retro", action="store_true")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--goal-met", dest="goal_met", action="store_true", default=None)
    g.add_argument("--goal-missed", dest="goal_met", action="store_false")
    x.set_defaults(func=cmd_sprint_close)
    sp.add_parser("cancel").set_defaults(func=cmd_sprint_cancel)
    sp.add_parser("list").set_defaults(func=cmd_sprint_list)
    x = sp.add_parser("show")
    x.add_argument("n", nargs="?")
    x.set_defaults(func=cmd_sprint_show)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["status"])
    args.func(args)


if __name__ == "__main__":
    main()

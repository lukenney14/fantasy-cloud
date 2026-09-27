# fantasy-cloud

A self-refreshing fantasy dashboard that lives on the internet, not on the Lenovo.

**The problem this solves:** Claude Desktop scheduled tasks only fire while the app is open
and the machine is awake, and live artifacts created after Aug 19 2026 don't render on
mobile at all. So neither one can put a current report on an iPhone. This repo does, using
GitHub's cron runners and GitHub Pages.

```
GitHub Actions cron  ->  build.py  ->  docs/index.html  ->  GitHub Pages  ->  iPhone home screen
   (GitHub's servers)      (stdlib)      (committed)         (static URL)      (Add to Home Screen)
```

No laptop. No Claude session. No dependencies to install.

---

## Setup — about five minutes, once

### 1. Create the repo

On GitHub, click **New repository**. Name it `fantasy-cloud`. **Private is fine** — Pages
works on private repos for Pro accounts; if you're on a free account, make it public (the
page exposes only your own league data, which is already visible to your leaguemates).

### 2. Push these files

If you have git installed:

```bash
cd fantasy-cloud
git init -b main
git add .
git commit -m "Initial fantasy cloud build"
git remote add origin https://github.com/<your-username>/fantasy-cloud.git
git push -u origin main
```

No git? Use the web uploader: on the empty repo page click **uploading an existing file**,
then drag in `build.py`, `README.md`, `ROUTINE_PROMPT.md`, the `docs` folder, and the
`.github` folder. GitHub's drag-and-drop preserves folder structure.

### 3. Turn on Pages

**Settings → Pages.** Under *Build and deployment*, set **Source** to `Deploy from a branch`,
**Branch** to `main`, and **Folder** to `/docs`. Save.

Your URL will be:

```
https://<your-username>.github.io/fantasy-cloud/
```

### 4. Let Actions write to the repo

**Settings → Actions → General → Workflow permissions.** Select **Read and write
permissions**, then Save. Without this the build runs but can't commit the page.

### 5. Run it once by hand

**Actions → Rebuild fantasy page → Run workflow.** Watch it. The *Show build log* step
prints everything `build.py` did, including any per-league failure. When it's green, reload
your Pages URL.

### 6. Add it to your phone

Open the URL in **Safari** → Share → **Add to Home Screen**. Done. The URL is permanent, so
that icon always opens the newest build.

---

## Refresh schedule

| When (Eastern) | Purpose |
| --- | --- |
| Tue 6:07 AM | Full weekly rebuild |
| Thu 6:07 AM | Midweek status |
| Sun 6:07 AM | Pre-gameday |
| Sun 11:07 AM | Inactives + survivor lock, before the 1pm kickoffs |

Actions cron is UTC and ignores daylight saving, so each time is scheduled twice — once at
the EDT offset and once at EST. `build.py` is idempotent, so the extra run is harmless.

GitHub also pauses cron on repos with no activity for 60 days. The weekly commits from this
workflow count as activity, so in practice it stays alive through the season.

---

## What gets built

Six CSS-only tabs, **zero JavaScript** (an earlier JS dashboard rendered blank in Gmail on
mobile, so the markup stays script-free and stays emailable):

- **W League** (ESPN, redraft, 10-team) — no kicker, nine starters
- **Beta** (ESPN, redraft, 14-team)
- **Wellington** (Sleeper, redraft, 12-team) — 5-pt pass TD plus yardage bonuses
- **Muppets** (Sleeper, **dynasty**, 10-team) — the only league where picks are currency
- **Values** — DynastyProcess 1QB values, Muppets only
- **About** — how the refresh works

Each league tab has a true side-by-side head-to-head (your player, your points, slot, their
points, their player), a free-points scan, injury designations on your starters, standings
by points scored, and your bench ranked by projection.

Projections are scored through **each league's own `scoring_settings`**, not Sleeper's
generic `pts_ppr`. That matters: Wellington's 5-point pass TD and 300/400-yard bonuses once
flipped Baker Mayfield ahead of Dak Prescott under the generic number.

---

## Notes and gotchas

- **Sleeper's `/rosters` endpoint serves a stale preseason snapshot for Muppets** (league
  status reads `pre_draft`, all records 0-0). `build.py` reads rosters from the live
  `/matchups` feed instead and only uses `/rosters` for owner-to-team-name mapping.
- **Every league is wrapped in its own try/except.** One dead API renders one error card,
  never a blank page. The job only exits non-zero if *every* league fails.
- Both ESPN leagues are `isPublic: true`, so there are no credentials anywhere in this repo
  and none are needed. Don't add any.
- `docs/data.json` is the structured output if you ever want to build something else on top.

## Editing

`build.py` is the whole program — one file, standard library only. The league registry is at
the top. Run it locally with `python3 build.py` and open `docs/index.html` to test changes
before pushing.

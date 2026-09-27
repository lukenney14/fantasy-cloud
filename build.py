#!/usr/bin/env python3
"""
Fantasy / Survivor / Betting — static site builder.

Pulls live data and renders docs/index.html as a ZERO-JAVASCRIPT, self-contained
page. Runs on GitHub Actions (cloud) so it refreshes with Luke's laptop off.

Design rules:
  * Standard library only. No pip install, nothing to break in CI.
  * Every league is wrapped in its own try/except. One dead API renders one
    error card, never a blank page.
  * Zero JS in the output. Gmail mobile strips <script>, and the page must also
    survive being emailed. CSS-only tabs.
  * Exit code is 0 unless EVERY league failed, so a partial outage still
    publishes a usable page.

Usage:  python3 build.py
Output: docs/index.html, docs/data.json, docs/build.log
"""

import datetime
import html
import json
import os
import sys
import traceback
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
UA = "fantasy-cloud/1.0 (+github actions static build)"
TIMEOUT = 45

SEASON = os.environ.get("FF_SEASON", "2026")

# --- league registry ---------------------------------------------------------
# Muppets is the ONLY dynasty league. Never reference future picks elsewhere.
SLEEPER_LEAGUES = [
    {
        "key": "wel",
        "name": "Wellington SZN5",
        "league_id": "1381311503352729600",
        "display_name": "lukekenney",
        "dynasty": False,
    },
    {
        "key": "mup",
        "name": "Muppets",
        "league_id": "1314047973029605376",
        "roster_id": 8,
        "dynasty": True,
    },
]

ESPN_LEAGUES = [
    # W League has NO kicker. mSettings reports a vestigial K:1 — ignore it.
    {"key": "wl", "name": "W League", "league_id": "1953672608", "team_id": 9},
    {"key": "beta", "name": "Beta Fantasy 2026", "league_id": "50437903", "team_id": 6},
]

ESPN_SLOT = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K",
    20: "BN", 21: "IR", 23: "FLEX",
}
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

# ESPN proTeamId -> abbreviation. Used so the bench table can show a team the
# same way the Sleeper leagues do.
ESPN_TEAM = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
    28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

LOG = []


def log(msg):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
    line = "[%s] %s" % (stamp, msg)
    LOG.append(line)
    print(line, flush=True)


def get(url, tries=3):
    """GET JSON with retries. Raises on final failure."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - want any transport error
            last = exc
            log("  fetch attempt %d/%d failed for %s: %s" % (attempt, tries, url[:90], exc))
    raise RuntimeError("giving up on %s: %s" % (url, last))


def get_text(url, tries=3):
    last = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            log("  text fetch attempt %d/%d failed: %s" % (attempt, tries, exc))
    raise RuntimeError("giving up on %s: %s" % (url, last))


# --- Sleeper -----------------------------------------------------------------

def sleeper_state():
    st = get("https://api.sleeper.app/v1/state/nfl")
    return int(st.get("week") or 1), str(st.get("season") or SEASON)


def sleeper_players():
    """5 MB payload. Trim hard so data.json stays small."""
    raw = get("https://api.sleeper.app/v1/players/nfl")
    out = {}
    for pid, p in raw.items():
        if not isinstance(p, dict):
            continue
        pos = p.get("position")
        if pos not in ("QB", "RB", "WR", "TE", "K", "DEF"):
            continue
        first = p.get("first_name") or ""
        last = p.get("last_name") or ""
        out[pid] = {
            "n": (first + " " + last).strip() or pid,
            "p": pos,
            "t": p.get("team") or "FA",
            "i": p.get("injury_status") or "",
        }
    log("  trimmed player map: %d entries" % len(out))
    return out


def sleeper_projections(season, week):
    url = "https://api.sleeper.app/v1/projections/nfl/regular/%s/%s" % (season, week)
    return get(url)


def score(stats, settings):
    """Score a Sleeper stat line under a league's own scoring_settings.

    Sleeper's projection keys and scoring_settings keys match, so this is exact,
    bonuses included. Never use pts_ppr directly: Wellington's 5-pt pass TD and
    yardage bonuses make the generic number wrong.
    """
    if not isinstance(stats, dict):
        return 0.0
    total = 0.0
    for key, mult in settings.items():
        val = stats.get(key)
        if isinstance(val, (int, float)):
            total += val * mult
    return round(total, 2)


def build_sleeper(cfg, players, projections, week, season):
    lid = cfg["league_id"]
    league = get("https://api.sleeper.app/v1/league/%s" % lid)
    users = get("https://api.sleeper.app/v1/league/%s/users" % lid)
    matchups = get("https://api.sleeper.app/v1/league/%s/matchups/%s" % (lid, week))

    settings = league.get("scoring_settings") or {}
    slots = [s for s in (league.get("roster_positions") or []) if s not in ("BN", "IR", "TAXI")]

    # Sleeper's /rosters endpoint has been observed serving a STALE preseason
    # snapshot for Muppets (status reads pre_draft, records 0-0). The matchups
    # feed is authoritative for who is actually on each roster right now.
    owner_by_roster = {}
    rosters = []
    try:
        rosters = get("https://api.sleeper.app/v1/league/%s/rosters" % lid)
        for r in rosters:
            owner_by_roster[r.get("roster_id")] = r.get("owner_id")
    except Exception as exc:  # noqa: BLE001
        log("  /rosters unavailable (%s); team names may be generic" % exc)

    name_by_user = {}
    for u in users:
        meta = u.get("metadata") or {}
        name_by_user[u.get("user_id")] = (meta.get("team_name") or u.get("display_name") or "Team").strip()

    def team_name(rid):
        return name_by_user.get(owner_by_roster.get(rid), "Roster %s" % rid)

    # Identify Luke's roster.
    mine = cfg.get("roster_id")
    if mine is None:
        want = (cfg.get("display_name") or "").lower()
        uid = next((u.get("user_id") for u in users
                    if (u.get("display_name") or "").lower() == want), None)
        mine = next((rid for rid, oid in owner_by_roster.items() if oid == uid), None)
    if mine is None:
        raise RuntimeError("could not identify Luke's roster in %s" % cfg["name"])

    by_roster = {m.get("roster_id"): m for m in matchups}
    me = by_roster.get(mine)
    if me is None:
        raise RuntimeError("roster %s has no week %s matchup" % (mine, week))

    opp_id = None
    for m in matchups:
        if m.get("roster_id") != mine and m.get("matchup_id") == me.get("matchup_id"):
            opp_id = m.get("roster_id")
            break
    opp = by_roster.get(opp_id) if opp_id else None

    def proj(pid):
        return score((projections or {}).get(str(pid)) or {}, settings)

    def row_for(m, idx):
        if not m:
            return ("—", 0.0, "")
        starters = m.get("starters") or []
        pid = starters[idx] if idx < len(starters) else None
        if not pid or pid in ("0", 0):
            return ("EMPTY", 0.0, "")
        # D/ST in Sleeper projections is keyed by team abbreviation.
        info = players.get(str(pid))
        if info:
            label = "%s %s" % (info["n"], info["t"])
            return (label, proj(pid), info["i"])
        return (str(pid), proj(pid), "")

    h2h = []
    my_total = opp_total = 0.0
    for i, slot in enumerate(slots):
        ln, lp, li = row_for(me, i)
        rn, rp, ri = row_for(opp, i)
        my_total += lp
        opp_total += rp
        h2h.append({"slot": slot, "me": ln, "mp": lp, "mi": li,
                    "opp": rn, "op": rp, "oi": ri})

    # Bench, ordered by projection so the free-points scan is obvious.
    starter_ids = set(str(x) for x in (me.get("starters") or []))
    bench = []
    for pid in (me.get("players") or []):
        if str(pid) in starter_ids:
            continue
        info = players.get(str(pid))
        if not info:
            continue
        bench.append({"n": info["n"], "p": info["p"], "t": info["t"],
                      "i": info["i"], "pr": proj(pid)})
    bench.sort(key=lambda b: -b["pr"])

    # Free-points scan: best bench player who out-projects a same-position starter.
    free = []
    for b in bench:
        for row in h2h:
            if row["slot"] in (b["p"], "FLEX") and b["pr"] - row["mp"] > 1.5:
                free.append("%s (%.1f) over %s (%.1f) at %s"
                            % (b["n"], b["pr"], row["me"], row["mp"], row["slot"]))
                break

    # Standings by points, computed from every completed week.
    standings = []
    try:
        totals = {}
        for wk in range(1, week):
            for m in get("https://api.sleeper.app/v1/league/%s/matchups/%s" % (lid, wk)):
                rid = m.get("roster_id")
                totals.setdefault(rid, {"pf": 0.0, "w": 0, "l": 0})
                totals[rid]["pf"] += float(m.get("points") or 0)
            # win/loss from the same pull
            pool = get("https://api.sleeper.app/v1/league/%s/matchups/%s" % (lid, wk))
            groups = {}
            for m in pool:
                groups.setdefault(m.get("matchup_id"), []).append(m)
            for pair in groups.values():
                if len(pair) == 2:
                    a, b = pair
                    pa, pb = float(a.get("points") or 0), float(b.get("points") or 0)
                    if pa == pb:
                        continue
                    win, lose = (a, b) if pa > pb else (b, a)
                    totals.setdefault(win["roster_id"], {"pf": 0.0, "w": 0, "l": 0})["w"] += 1
                    totals.setdefault(lose["roster_id"], {"pf": 0.0, "w": 0, "l": 0})["l"] += 1
        for rid, t in totals.items():
            standings.append({"team": team_name(rid), "w": t["w"], "l": t["l"],
                              "pf": round(t["pf"], 2), "me": rid == mine})
        standings.sort(key=lambda s: -s["pf"])
    except Exception as exc:  # noqa: BLE001
        log("  standings skipped for %s: %s" % (cfg["name"], exc))

    return {
        "key": cfg["key"], "name": cfg["name"], "platform": "Sleeper",
        "dynasty": cfg.get("dynasty", False), "week": week,
        "me": team_name(mine), "opp": team_name(opp_id) if opp_id else "BYE",
        "my_total": round(my_total, 2), "opp_total": round(opp_total, 2),
        "h2h": h2h, "bench": bench[:14], "free": free, "standings": standings,
        "ok": True,
    }


# --- ESPN --------------------------------------------------------------------

def build_espn(cfg, week):
    url = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%s"
           "/segments/0/leagues/%s?view=mRoster&view=mTeam&view=mSettings"
           "&view=mMatchupScore&scoringPeriodId=%s" % (SEASON, cfg["league_id"], week))
    data = get(url)

    teams = {}
    for t in data.get("teams") or []:
        nm = (t.get("name") or "").strip()
        if not nm:
            nm = ((t.get("location") or "") + " " + (t.get("nickname") or "")).strip()
        teams[t.get("id")] = nm or ("Team %s" % t.get("id"))

    def weekly(entry):
        """Weekly projection only. statSplitTypeId 0 is the SEASON total —
        using it for a start/sit has produced wrong answers before."""
        pool = ((entry.get("playerPoolEntry") or {}).get("player") or {})
        for s in pool.get("stats") or []:
            if (str(s.get("seasonId")) == str(SEASON)
                    and s.get("statSourceId") == 1
                    and s.get("statSplitTypeId") == 1
                    and int(s.get("scoringPeriodId") or -1) == int(week)):
                return round(float(s.get("appliedTotal") or 0), 2)
        return 0.0

    def roster_rows(team_id):
        for t in data.get("teams") or []:
            if t.get("id") != team_id:
                continue
            entries = ((t.get("roster") or {}).get("entries") or [])
            starters, bench = [], []
            for e in entries:
                pool = ((e.get("playerPoolEntry") or {}).get("player") or {})
                slot = ESPN_SLOT.get(e.get("lineupSlotId"), str(e.get("lineupSlotId")))
                row = {
                    "n": pool.get("fullName") or "—",
                    "p": ESPN_POS.get(pool.get("defaultPositionId"), "?"),
                    "t": ESPN_TEAM.get(pool.get("proTeamId"), ""),
                    "slot": slot,
                    "pr": weekly(e),
                    "i": (pool.get("injuryStatus") or "").title().replace("_", " "),
                }
                # W League has no kicker; never surface an empty K slot there.
                if cfg["key"] == "wl" and row["slot"] == "K":
                    continue
                (bench if slot in ("BN", "IR") else starters).append(row)
            bench.sort(key=lambda b: -b["pr"])
            return starters, bench
        return [], []

    my_starters, my_bench = roster_rows(cfg["team_id"])

    opp_id = None
    for m in data.get("schedule") or []:
        if int(m.get("matchupPeriodId") or 0) != int(week):
            continue
        home = (m.get("home") or {}).get("teamId")
        away = (m.get("away") or {}).get("teamId")
        if home == cfg["team_id"]:
            opp_id = away
        elif away == cfg["team_id"]:
            opp_id = home
        if opp_id:
            break
    opp_starters, _ = roster_rows(opp_id) if opp_id else ([], [])

    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4, "D/ST": 5, "K": 6}
    my_starters.sort(key=lambda r: order.get(r["slot"], 9))
    opp_starters.sort(key=lambda r: order.get(r["slot"], 9))

    h2h = []
    for i in range(max(len(my_starters), len(opp_starters))):
        a = my_starters[i] if i < len(my_starters) else None
        b = opp_starters[i] if i < len(opp_starters) else None
        h2h.append({
            "slot": (a or b or {}).get("slot", "—"),
            "me": a["n"] if a else "—", "mp": a["pr"] if a else 0.0,
            "mi": a["i"] if a else "",
            "opp": b["n"] if b else "—", "op": b["pr"] if b else 0.0,
            "oi": b["i"] if b else "",
        })

    free = []
    for b in my_bench:
        for row in h2h:
            if row["slot"] in (b["p"], "FLEX") and b["pr"] - row["mp"] > 1.5:
                free.append("%s (%.1f) over %s (%.1f) at %s"
                            % (b["n"], b["pr"], row["me"], row["mp"], row["slot"]))
                break

    standings = []
    for t in data.get("teams") or []:
        rec = ((t.get("record") or {}).get("overall") or {})
        standings.append({
            "team": teams.get(t.get("id"), "?"),
            "w": int(rec.get("wins") or 0), "l": int(rec.get("losses") or 0),
            "pf": round(float(rec.get("pointsFor") or 0), 2),
            "me": t.get("id") == cfg["team_id"],
        })
    standings.sort(key=lambda s: -s["pf"])

    return {
        "key": cfg["key"], "name": cfg["name"], "platform": "ESPN",
        "dynasty": False, "week": week,
        "me": teams.get(cfg["team_id"], "My team"),
        "opp": teams.get(opp_id, "BYE") if opp_id else "BYE",
        "my_total": round(sum(r["mp"] for r in h2h), 2),
        "opp_total": round(sum(r["op"] for r in h2h), 2),
        "h2h": h2h, "bench": my_bench[:14], "free": free,
        "standings": standings, "ok": True,
    }


# --- dynasty values (Muppets only) -------------------------------------------

def dynasty_values():
    """DynastyProcess values.csv — compact, additive, and includes picks."""
    txt = get_text("https://raw.githubusercontent.com/dynastyprocess/data/master/files/values.csv")
    rows = []
    lines = txt.splitlines()
    if not lines:
        return rows
    hdr = [c.strip('"') for c in lines[0].split(",")]
    idx = {name: i for i, name in enumerate(hdr)}
    for line in lines[1:]:
        # values.csv has no embedded commas inside quoted player names in practice,
        # but be defensive: only split when field count matches.
        parts = [c.strip('"') for c in line.split(",")]
        if len(parts) != len(hdr):
            continue
        try:
            rows.append({
                "player": parts[idx["player"]],
                "pos": parts[idx["pos"]],
                "team": parts[idx["team"]],
                "age": parts[idx["age"]],
                "v1qb": int(float(parts[idx["value_1qb"]] or 0)),
            })
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda r: -r["v1qb"])
    log("  dynasty values: %d rows" % len(rows))
    return rows


# --- rendering ---------------------------------------------------------------

def esc(s):
    return html.escape(str(s if s is not None else ""))


CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#eef1f5;color:#16202e;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;-webkit-text-size-adjust:100%}
.shell{max-width:1060px;margin:0 auto;padding:14px}
h1{font-size:20px;margin:0 0 2px;letter-spacing:-.3px}
.sub{color:#5a6b80;font-size:12px;margin-bottom:10px}
.strip{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.src{background:#fff;border:1px solid #d8dfe7;border-radius:5px;padding:4px 7px;font-size:10.5px;line-height:1.3}
.src b{display:block;font-size:10px}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px}
.g{background:#1e9e51}.a{background:#d99100}.r{background:#c0392b}
input.t{position:absolute;opacity:0;pointer-events:none;width:0;height:0}
.tabs{display:flex;flex-wrap:wrap;gap:3px;margin-bottom:-1px}
.tabs label{background:#dbe2ea;color:#3c4a5c;padding:7px 11px;border-radius:6px 6px 0 0;font-size:12px;font-weight:600;cursor:pointer;border:1px solid #cfd7e0;border-bottom:none}
.wrap{background:#fff;border:1px solid #cfd7e0;border-radius:0 7px 7px 7px;padding:13px}
.pg{display:none}
#l0:checked~.wrap #p0,#l1:checked~.wrap #p1,#l2:checked~.wrap #p2,#l3:checked~.wrap #p3,#l4:checked~.wrap #p4,#l5:checked~.wrap #p5{display:block}
#l0:checked~.tabs label[for=l0],#l1:checked~.tabs label[for=l1],#l2:checked~.tabs label[for=l2],#l3:checked~.tabs label[for=l3],#l4:checked~.tabs label[for=l4],#l5:checked~.tabs label[for=l5]{background:#fff;color:#101f38;border-color:#cfd7e0}
h2{font-size:15px;margin:0 0 3px}
h3{font-size:12px;margin:15px 0 5px;color:#20364f;text-transform:uppercase;letter-spacing:.5px}
table{border-collapse:collapse;width:100%;margin:5px 0 9px;font-size:12.5px}
th{background:#16202e;color:#fff;text-align:left;padding:5px 7px;font-size:10.5px;font-weight:600}
td{border-bottom:1px solid #e2e8ef;padding:4px 7px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
td.c,th.c{text-align:center}
.slot{background:#16202e;color:#fff;font-size:10px;font-weight:700;text-align:center;letter-spacing:.4px}
.hi{background:#e3f5e8;font-weight:700}
.me{background:#e3f5e8 !important;font-weight:700}
.sc{display:flex;gap:8px;align-items:baseline;margin:6px 0}
.sc div{flex:1;font-size:12px;color:#5a6b80}
.sc b{font-size:19px;color:#16202e;display:block;font-variant-numeric:tabular-nums}
.card{border-left:4px solid #b8860b;background:#fffaf0;padding:8px 11px;margin:8px 0;font-size:12.5px;border-radius:0 4px 4px 0}
.card.ok{border-color:#1e9e51;background:#f0f9f3}
.card.bad{border-color:#c0392b;background:#fdf4f4}
.inj{color:#c0392b;font-size:10px;font-weight:700;margin-left:3px}
.small{font-size:11px;color:#5a6b80}
@media(max-width:620px){
  .shell{padding:8px}
  .tabs label{padding:6px 8px;font-size:11px}
  table{font-size:11.5px}
  td,th{padding:3px 4px}
  .sc b{font-size:16px}
}
"""


def h2h_table(lg):
    out = ['<div class="sc"><div>%s<b>%.2f</b></div><div style="text-align:right">%s<b>%.2f</b></div></div>'
           % (esc(lg["me"]), lg["my_total"], esc(lg["opp"]), lg["opp_total"])]
    out.append('<table><tr><th>%s</th><th class="n">Pts</th><th class="c">Slot</th>'
               '<th class="n">Pts</th><th>%s</th></tr>' % (esc(lg["me"]), esc(lg["opp"])))
    for r in lg["h2h"]:
        lcls = ' class="hi"' if r["mp"] > r["op"] else ""
        rcls = ' class="hi"' if r["op"] > r["mp"] else ""
        mi = '<span class="inj">%s</span>' % esc(r["mi"]) if r["mi"] else ""
        oi = '<span class="inj">%s</span>' % esc(r["oi"]) if r["oi"] else ""
        out.append(
            "<tr><td%s>%s%s</td><td class=\"n\"%s>%.2f</td><td class=\"slot\">%s</td>"
            "<td class=\"n\"%s>%.2f</td><td%s>%s%s</td></tr>"
            % (lcls, esc(r["me"]), mi, lcls, r["mp"], esc(r["slot"]),
               rcls, r["op"], rcls, esc(r["opp"]), oi))
    out.append("</table>")
    return "".join(out)


def league_pane(lg):
    if not lg.get("ok"):
        return ('<h2>%s</h2><div class="card bad"><b>This league did not build.</b><br>'
                '<span class="small">%s</span><br><span class="small">The other tabs are '
                'unaffected. Check the Actions log for the traceback.</span></div>'
                % (esc(lg["name"]), esc(lg.get("error", "unknown error"))))

    parts = ['<h2>%s</h2><div class="small">%s &middot; Week %s &middot; %s</div>'
             % (esc(lg["name"]), esc(lg["platform"]), lg["week"],
                "DYNASTY" if lg["dynasty"] else "redraft")]

    parts.append("<h3>Head to head</h3>")
    parts.append(h2h_table(lg))

    parts.append("<h3>Free points scan</h3>")
    if lg["free"]:
        parts.append('<div class="card"><b>Lineup is leaving points on the bench:</b><ul>')
        for f in lg["free"]:
            parts.append("<li>%s</li>" % esc(f))
        parts.append("</ul></div>")
    else:
        parts.append('<div class="card ok">Lineup is optimal by projection '
                     '(no bench player beats a starter by more than 1.5).</div>')

    inj = [r for r in lg["h2h"] if r["mi"]]
    if inj:
        parts.append("<h3>Designations on your starters</h3><table>"
                     "<tr><th>Player</th><th>Slot</th><th>Status</th><th class=\"n\">Proj</th></tr>")
        for r in inj:
            parts.append('<tr><td>%s</td><td>%s</td><td>%s</td><td class="n">%.2f</td></tr>'
                         % (esc(r["me"]), esc(r["slot"]), esc(r["mi"]), r["mp"]))
        parts.append("</table>")

    if lg["standings"]:
        parts.append("<h3>Standings by points scored</h3><table>"
                     "<tr><th>Team</th><th class=\"n\">W</th><th class=\"n\">L</th><th class=\"n\">Points</th></tr>")
        for s in lg["standings"]:
            cls = ' class="me"' if s["me"] else ""
            parts.append('<tr%s><td>%s</td><td class="n">%d</td><td class="n">%d</td>'
                         '<td class="n">%.2f</td></tr>'
                         % (cls, esc(s["team"]), s["w"], s["l"], s["pf"]))
        parts.append("</table>")

    if lg["bench"]:
        parts.append("<h3>Bench, by projection</h3><table>"
                     "<tr><th>Player</th><th>Pos</th><th>Team</th><th>Status</th><th class=\"n\">Proj</th></tr>")
        for b in lg["bench"]:
            # .get() throughout: ESPN and Sleeper rows carry slightly different
            # keys, and a missing one must not take down the whole page.
            parts.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class="n">%.2f</td></tr>'
                         % (esc(b.get("n")), esc(b.get("p")), esc(b.get("t")),
                            esc(b.get("i")), b.get("pr", 0.0)))
        parts.append("</table>")

    if lg["dynasty"]:
        parts.append('<div class="card"><b>Dynasty league.</b> This is the only one of the four '
                     'where future draft picks are currency. Values are on the Values tab.</div>')
    return "".join(parts)


def values_pane(vals, err=None):
    if err:
        return ('<h2>Dynasty values</h2><div class="card bad">Value feed failed: %s</div>' % esc(err))
    picks = [v for v in vals if v["pos"] == "PICK"][:26]
    players = [v for v in vals if v["pos"] != "PICK"][:60]
    out = ['<h2>Dynasty values &mdash; Muppets only</h2>'
           '<div class="small">DynastyProcess 1QB values. Additive by construction, so these '
           'can be summed to price a package &mdash; unlike KeepTradeCut, whose floor is so high '
           'that adding two values overstates the second piece.</div>']
    out.append("<h3>Picks</h3><table><tr><th>Pick</th><th class=\"n\">Value (1QB)</th></tr>")
    for p in picks:
        out.append('<tr><td>%s</td><td class="n">%d</td></tr>' % (esc(p["player"]), p["v1qb"]))
    out.append("</table>")
    out.append("<h3>Top 60 players</h3><table>"
               "<tr><th>Player</th><th>Pos</th><th>Team</th><th class=\"n\">Age</th><th class=\"n\">Value</th></tr>")
    for p in players:
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td><td class="n">%s</td><td class="n">%d</td></tr>'
                   % (esc(p["player"]), esc(p["pos"]), esc(p["team"]), esc(p["age"]), p["v1qb"]))
    out.append("</table>")
    return "".join(out)


def render(leagues, vals, vals_err, week, built):
    tabs = [lg["name"] for lg in leagues] + ["Values", "About"]
    tabs = tabs[:6]
    while len(tabs) < 6:
        tabs.append("—")

    def freshness():
        now = built.strftime("%a %b %d, %I:%M %p UTC")
        rows = [
            ("Sleeper", "Wellington, Muppets", "g"),
            ("ESPN Fantasy", "W League, Beta", "g"),
            ("DynastyProcess", "Muppets values", "g" if not vals_err else "r"),
        ]
        cells = ['<div class="src"><b>Built</b>%s</div>' % esc(now)]
        for name, drives, state in rows:
            cells.append('<div class="src"><b><span class="dot %s"></span>%s</b>%s</div>'
                         % (state, esc(name), esc(drives)))
        return '<div class="strip">%s</div>' % "".join(cells)

    panes = []
    for i, lg in enumerate(leagues[:4]):
        panes.append('<div class="pg" id="p%d">%s</div>' % (i, league_pane(lg)))
    panes.append('<div class="pg" id="p4">%s</div>' % values_pane(vals, vals_err))
    panes.append('<div class="pg" id="p5">%s</div>' % about_pane(built, week))
    while len(panes) < 6:
        panes.append('<div class="pg" id="p%d"></div>' % len(panes))

    radios = "".join(
        '<input class="t" type="radio" name="tab" id="l%d"%s>' % (i, " checked" if i == 0 else "")
        for i in range(6))
    labels = "".join('<label for="l%d">%s</label>' % (i, esc(tabs[i])) for i in range(6))

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Fantasy">
<meta name="theme-color" content="#16202e">
<link rel="manifest" href="manifest.webmanifest">
<title>Fantasy / Survivor / Betting Landing Page</title>
<style>%s</style>
</head>
<body><div class="shell">
<h1>Fantasy / Survivor / Betting</h1>
<div class="sub">Luke Kenney &middot; Week %s &middot; rebuilt automatically in the cloud, laptop off</div>
%s
%s
<div class="tabs">%s</div>
<div class="wrap">%s</div>
</div></body></html>""" % (CSS, week, freshness(), radios, labels, "".join(panes))


def about_pane(built, week):
    return """<h2>How this page stays current</h2>
<div class="card ok"><b>Nothing here depends on the Lenovo being on.</b>
A GitHub Actions cron job runs <code>build.py</code> on GitHub's servers, pulls every feed,
and commits the rendered page back to the repo. GitHub Pages serves it. Your phone just
loads a static file.</div>
<h3>Refresh schedule</h3>
<table><tr><th>When (Pacific)</th><th>What runs</th></tr>
<tr><td>Tue 6:07 AM</td><td>Full rebuild &mdash; the deep weekly report</td></tr>
<tr><td>Thu 6:07 AM</td><td>Short rebuild &mdash; midweek status</td></tr>
<tr><td>Sun 6:07 AM</td><td>Short rebuild &mdash; pre-gameday</td></tr>
<tr><td>Sun 8:07 AM</td><td>Inactives and survivor lock, before the 10am PT kickoffs</td></tr>
</table>
<h3>Zero JavaScript, on purpose</h3>
<p class="small">The tabs are CSS-only radio buttons. Gmail mobile strips <code>&lt;script&gt;</code>,
so the same markup can be emailed without rendering blank &mdash; which is what happened to an
earlier JS dashboard.</p>
<h3>Add to your home screen</h3>
<p class="small">iPhone: open this URL in Safari, tap Share, then <b>Add to Home Screen</b>.
The URL never changes, so the icon always opens the newest build.</p>
<h3>Known data caveat</h3>
<p class="small">Sleeper's <code>/rosters</code> endpoint has been observed serving a stale
preseason snapshot for Muppets. This builder reads rosters from the live
<code>/matchups</code> feed instead.</p>
<p class="small">Built %s &middot; week %s.</p>""" % (
        esc(built.strftime("%Y-%m-%d %H:%M UTC")), week)


def main():
    os.makedirs(DOCS, exist_ok=True)
    built = datetime.datetime.now(datetime.timezone.utc)
    log("build start, season %s" % SEASON)

    try:
        week, season = sleeper_state()
    except Exception as exc:  # noqa: BLE001
        log("could not read NFL state (%s); defaulting to week 1" % exc)
        week, season = 1, SEASON
    log("week %s, season %s" % (week, season))

    players, projections = {}, {}
    try:
        players = sleeper_players()
        projections = sleeper_projections(season, week)
        log("projections: %d entries" % len(projections or {}))
    except Exception as exc:  # noqa: BLE001
        log("sleeper support data failed: %s" % exc)

    leagues = []
    for cfg in SLEEPER_LEAGUES:
        try:
            log("building %s (Sleeper)" % cfg["name"])
            leagues.append(build_sleeper(cfg, players, projections, week, season))
        except Exception as exc:  # noqa: BLE001
            log("FAILED %s: %s" % (cfg["name"], exc))
            log(traceback.format_exc())
            leagues.append({"key": cfg["key"], "name": cfg["name"], "ok": False,
                            "error": str(exc)})

    for cfg in ESPN_LEAGUES:
        try:
            log("building %s (ESPN)" % cfg["name"])
            leagues.append(build_espn(cfg, week))
        except Exception as exc:  # noqa: BLE001
            log("FAILED %s: %s" % (cfg["name"], exc))
            log(traceback.format_exc())
            leagues.append({"key": cfg["key"], "name": cfg["name"], "ok": False,
                            "error": str(exc)})

    vals, vals_err = [], None
    try:
        vals = dynasty_values()
    except Exception as exc:  # noqa: BLE001
        vals_err = str(exc)
        log("dynasty values failed: %s" % exc)

    # Order tabs: W League, Beta, Wellington, Muppets.
    order = {"wl": 0, "beta": 1, "wel": 2, "mup": 3}
    leagues.sort(key=lambda lg: order.get(lg["key"], 9))

    page = render(leagues, vals, vals_err, week, built)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as fh:
        json.dump({"built": built.isoformat(), "week": week, "leagues": leagues},
                  fh, indent=1)
    with open(os.path.join(DOCS, "build.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(LOG))

    good = sum(1 for lg in leagues if lg.get("ok"))
    log("wrote docs/index.html (%d bytes), %d/%d leagues OK"
        % (len(page), good, len(leagues)))
    if good == 0:
        log("every league failed — exiting non-zero so Actions flags it")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

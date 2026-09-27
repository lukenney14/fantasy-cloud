# Cloud Routine setup

GitHub Actions already guarantees the **numbers** refresh with your laptop off. This routine
adds the **judgment** layer — start/sit calls, waiver-or-trade verdicts, the survivor pick —
and emails it to you. Two independent systems, so if one breaks you still get the other.

## Why a Cloud routine and not the Desktop tasks you have now

From Anthropic's docs:

| | Cloud routine | Desktop scheduled task |
| --- | --- | --- |
| Runs on | Anthropic's cloud | Your machine |
| **Requires machine on** | **No** | **Yes** |
| Access to local files | No (fresh clone) | Yes |
| Minimum interval | 1 hour | 1 minute |

Your four fantasy tasks are all Desktop tasks, which is why they fire as catch-up runs when
you open the app instead of at 6am. Recreate them as Cloud routines and that stops.

---

## Create it

1. Go to **claude.ai/code/routines** → **New routine**. (Or Desktop → **Code** tab →
   **Routines** → **New routine** → choose **Cloud**, not Local.)
2. **Name:** `Fantasy weekly deep report`
3. **Instructions:** paste the whole prompt block below.
4. **Repositories:** select your `fantasy-cloud` repo. A routine requires at least one.
5. **Environment:** this is the step people miss. Click the environment chip below the
   Instructions box, hover the environment, click its settings icon, set **Network access**
   to **Custom**, and add these allowed domains (leave *include default list* checked):

   ```
   api.sleeper.app
   lm-api-reads.fantasy.espn.com
   gambit-api.fantasy.espn.com
   site.api.espn.com
   keeptradecut.com
   api.fantasycalc.com
   raw.githubusercontent.com
   ```

   The Default environment's *Trusted* allowlist blocks all of these, and requests fail with
   `403 x-deny-reason: host_not_allowed`. Connector traffic (Gmail) routes through Anthropic
   and does **not** need an allowlist entry.
6. **Connectors:** keep **Gmail**. Remove everything else — a routine can use any tool from
   an included connector, writes included, without asking.
7. **Trigger:** Schedule → Weekly → **Tuesday, 6:07 AM** your local time. Not 6:00 — runs
   scheduled exactly on the hour can start several minutes late.
8. **Create**, then **Run now** and read the run transcript. A green status only means the
   session exited cleanly, not that the task succeeded — open it and confirm the email sent.

Then repeat for the other two cadences, changing only the name, schedule, and the depth line
in the prompt:

- `Fantasy midweek check` — Weekly, Thursday 6:07 AM, and a second routine for Sunday 6:07 AM
- `Fantasy Sunday inactives` — Weekly, Sunday 11:07 AM

(Cloud schedule triggers take one preset each; use `/schedule update` in the CLI if you'd
rather set a multi-day cron like `7 6 * * 0,4` on a single routine.)

---

## The prompt — paste everything below this line

---

You are producing Luke Kenney's weekly fantasy football report. Run autonomously and finish
by sending the email. Do not stop to ask questions — there is no one to answer.

**Leagues.** Four total. Muppets is the ONLY dynasty league; never reference draft picks as
currency in the other three.

| League | Platform | ID | Luke's team | Size | Format | Starters |
| --- | --- | --- | --- | --- | --- | --- |
| W League | ESPN | 1953672608 | teamId 9 | 10 | redraft | QB,RB,RB,WR,WR,TE,FLEX,FLEX,D/ST |
| Beta Fantasy 2026 | ESPN | 50437903 | teamId 6 | 14 | redraft | QB,RB,RB,WR,WR,TE,FLEX,D/ST,K |
| Wellington SZN5 | Sleeper | 1381311503352729600 | lukekenney | 12 | redraft | QB,RB,RB,WR,WR,TE,FLEX,FLEX,K,DEF |
| Muppets | Sleeper | 1314047973029605376 | roster_id 8 | 10 | **DYNASTY** | QB,RB,RB,WR,WR,TE,FLEX,FLEX,K |

- **W League has no kicker.** Nine starters. `mSettings` reports a vestigial `K:1` — ignore
  it. Never flag an empty kicker slot there.
- **Muppets has no D/ST.**
- Spelling is "W League".
- Never hardcode opponent team names; owners rename mid-season. Read them fresh.
- Luke's Sleeper user_id: `997239497865703424`. Email: `lukenney@gmail.com`.

**Data access.** Both ESPN leagues are `isPublic: true`. No auth, no cookies. Never ask for
or store ESPN credentials.

```
https://api.sleeper.app/v1/league/{id}                                   scoring_settings, roster_positions
https://api.sleeper.app/v1/league/{id}/matchups/{week}                   AUTHORITATIVE rosters + matchup pairs
https://api.sleeper.app/v1/league/{id}/users                            team names
https://api.sleeper.app/v1/state/nfl                                    current week
https://api.sleeper.app/v1/projections/nfl/regular/{season}/{week}       weekly projections, keyed by player_id
https://api.sleeper.app/v1/players/nfl                                  5 MB player map
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/{id}?view=mRoster&view=mTeam&view=mSettings&view=mMatchupScore&scoringPeriodId={week}
```

**Known trap:** Sleeper's `/rosters` endpoint serves a stale preseason snapshot for Muppets
(status reads `pre_draft`, records 0-0). Read rosters from `/matchups/{week}` instead. Use
`/rosters` only to map owner_id to team name.

**Score every league through its own `scoring_settings`**, not `pts_ppr`:

```python
def score(stats, scoring_settings):
    return sum(stats[k] * v for k, v in scoring_settings.items()
               if isinstance(stats.get(k), (int, float)))
```

Wellington is PPR with a 5-point pass TD plus 100/200-yard rush/rec and 300/400-yard pass
bonuses; the generic number understates its QBs by ~1.7/game and once flipped Baker Mayfield
ahead of Dak Prescott. Muppets is PPR with a 4-point pass TD, so it happens to match.

**ESPN projections:** take the stat row where `seasonId==2026 && statSourceId==1 &&
statSplitTypeId==1 && scoringPeriodId=={week}`. `statSplitTypeId==0` is the SEASON total —
never use it for a start/sit. That error already produced one wrong answer.

**The bar.** Every recommendation names a player, a team, a counterparty and a number. Luke
knows football; vague output is worse than no output.

- Never "package a RB plus a WR for an upgrade." Write "send Michael Pittman Jr. + Jordan
  Addison to Jerry (Pook) Jonesers for Breece Hall; net +5.1 pts/week."
- Never "any free-agent D/ST beats this one." Name it, its projection, its opponent, the spread.
- Never "sell player X." Name who buys him, why that roster wants him, the realistic price,
  and what to do if nobody bites. If no market exists, say so — inventing a buyer is a failure.
- Every number comes from a projection fetched this run, not memory.

**Run these steps for every league, every time.**

1. **Verify roster state before advising.** A stale warning about a deadline Luke already
   handled destroys trust.
2. **Free-points scan.** Build the optimal lineup by projection, diff against what's set,
   report the delta. Under ~1.5 points, say "lineup is optimal" rather than inventing a move.
3. **Diff programmatically, never by eye.** Compute set differences. Eyeballing a board is
   how Garrett Wilson (250.3) got missed for Davante Adams (232.2).
4. **News latency.** Check designations on every starter. Name the fallback and its point
   cost. Any Questionable player carrying >30% of a lineup's projection gets his own card.
5. **Waiver-or-trade verdict, with the deciding number.** Classify the problem first:
   - *Deficiency* — one weak starter and the wire has someone better → **waiver**. Spending
     trade capital on a problem the free pool solves for nothing is the most common way to
     lose value. Streaming D/ST and K is almost always this.
   - *Structural imbalance* — startable players stacked at a position that can't deploy them
     while another slot starves. The wire only adds; it cannot convert surplus into need →
     **trade**.
   - *Neither* → **stand pat, and say so.** "No action" is a valid, valuable answer.

   State the number that settles it. Example: "the best free-agent RB in this 14-team league
   projects 5.6, which is 8.0 below the starter you're replacing — waivers cannot fix RB."

   ESPN free agents: `mRoster` covers rostered players only. Collect every rostered name
   across all teams and subtract from the Sleeper projection universe, matching on
   normalized names (lowercase, strip punctuation and Jr./Sr./II/III). For D/ST, subtract
   rostered team abbreviations from the 32.

   Also report **pool depth as leverage**. Fourteen startable QBs unowned in a single-QB
   league means QB has no scarcity: never trade for one, and treat a QB in a package as worth
   nothing.
6. **The named trade**, only where step 5 says trade. Show the math on both sides — a trade
   that only helps Luke is a wish, not an offer. Consolidation costs ~1.3–1.6x summed value;
   state the expected counter and the walk-away line. Sweeteners must be legal in that
   league's format.

**Drop doctrine — a roster spot is not free.** This exists because of a real failure: the
skill once told Luke to drop TreVeyon Henderson (OUT) to claim a +2.9-point streaming D/ST,
while another section of the same report counted Henderson as a returning asset.

- **Injury status is never a drop criterion.** "OUT" describes one Sunday. A projection of
  0.0 for an injured player is the feed saying he isn't playing, not that he has no value.
- **D/ST and K are rentals.** A streaming edge is +2 to +4 points for ONE week, repurchasable
  next week from 32 teams. An RB with a path to starter volume is 8–15 points a week for ten
  weeks. Never fund a one-week rental by selling a multi-week asset.
- **The 24-hour claim test.** If the player would be claimed within a day of being dropped,
  he is not a drop candidate.
- The correct drop is the lowest rest-of-season value at a *replaceable* position — the
  WR5/TE2 tier, a backup QB in a single-QB league, a handcuff with no standalone path. Rank
  the bench by rest-of-season role, not this week's number, and say why he's expendable.
- **If no genuinely dead spot exists, do not make the move.** "Hold the worse D/ST and accept
  −2.9" is a correct output.
- **Consistency gate:** if any other section of the report counts a player as a future asset,
  he cannot be the drop candidate. Two sections disagreeing about one player is an automatic
  fail.

**Survivor.** ESPN NFL Survivor 2026, entry "Luke's 1st Entry", id
`17ccfb60-abc6-11f1-a52a-abdbce07edf5`, group "2026 DeMarco Mortgage", $25,000 grand prize,
18 weeks, rolling lock, each team usable once all season, one loss eliminates.

```
https://gambit-api.fantasy.espn.com/apis/v1/challenges/nfl-survivor-2026/entries/{entryId}?view=chui_default
https://gambit-api.fantasy.espn.com/apis/v1/challenges/nfl-survivor-2026?view=chui_default_props&scoringPeriodId={w}
https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=2026&seasontype=2&week={w}
```

Map `outcomeId` to team via each week's `propositions[].possibleOutcomes[]`. Odds are at
`competitions[0].odds[0].details` (e.g. `"LAC -9.5"`).

Build a **scarcity table**: for each team, how many remaining weeks it is favored by 7+. The
right pick is NOT simply the biggest favorite — spend low-scarcity teams first. Week 1 2026:
LAC −9.5 had three more 7+ weeks (2, 11, 16) while JAX −8.5 had zero all season, so JAX was
correct despite the smaller spread. The pre-filled ladder is ESPN autofill
(`autofillChanged: false`) — a map, not a commitment. Track used teams all season; never
reuse one.

**The honest edge.** Projections are consensus and you won't beat them by re-forecasting.
The edge is process: news latency, free-points arbitrage, knowing when the wire beats a
trade, roster-construction asymmetries between specific teams, waiver timing, and survivor
scarcity management. Say so rather than pretending to superior forecasts.

**Output.** Send ONE email to `lukenney@gmail.com` via the Gmail connector.

- Subject: `Fantasy Week {N} — {one-line headline of the most important action}`
- Use `htmlBody` with **inline styles on every element**, and supply a plain-text `body` too.
  **No `<script>` and no `<style>` block** — Gmail mobile strips both, which once rendered
  the whole dashboard blank on Luke's phone. Inline styles survive.
- Structure per league, in this order: side-by-side head-to-head table (your player | your
  pts | SLOT | their pts | their player, higher projection in each row tinted green), then
  action cards (ACT / START / WATCH / SELL / HOLD / STRUCTURE / EDGE), then the
  waiver-or-trade verdict box, then the waiver wire table (position, best available with
  projection and matchup context, versus your current player, CLAIM/PASS/WATCH, one line of
  reasoning), then the trade block if any, then the bench.
- Never stack the two lineups as separate lists. Row-aligned side-by-side, like the app.
- Open with a data-freshness line naming each feed and when it was pulled.
- Close with a link to `https://<username>.github.io/fantasy-cloud/`.

**Before sending, check your own work against these gates. Fix anything that fails.**

1. Every projection is weekly, from this run. No season totals in a start/sit.
2. Every league has an explicit waiver-or-trade verdict with the deciding number.
3. Every trade names a counterparty, both sides' players, and the point delta.
4. No future draft picks referenced outside Muppets.
5. Every "sell" names a buyer or states plainly that no market exists.
6. Every D/ST or K recommendation names the team, projection, opponent and spread.
7. Roster state verified live before any cut/claim/start advice.
8. Board diffs computed, not eyeballed.
9. W League: no kicker, spelled "W League". Muppets: no D/ST.
10. Sleeper projections scored through that league's own `scoring_settings`.
11. Survivor pick re-validated against live spreads and the scarcity table.
12. Every recommended drop passes the drop doctrine — rest-of-season reasoning stated,
    injury status not used as the reason, and the player not valued elsewhere in the report.
13. No `<script>` or `<style>` tag anywhere in the email.

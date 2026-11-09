# Match formats and procedures, one rulebook at a time (built in; please check against your copies)

Every format belongs to ONE rulebook, and its procedures, rule references, screen texts, tie-breaks and finals come
from that book only (fetcore/rulebooks.py). A book that does not cover something leaves it available, with no rule
cited: "the match director decides". A custom format ("Design your own match") must name its book, or "No rulebook".
Custom formats saved before 0.6.29 have no book until they are edited (they run as "No rulebook").

Every time below is read from the rulebook named, with the rule number, so it can be checked against your copy.
"Pause" is the short gap while electronic targets switch from sighting to record; no target-change time is used
anywhere (electronic targets). Times are minutes unless marked.

| Book (id) | Source | Formats | Finals built |
|---|---|---|---|
| **ISSF** (`ISSF`) | ISSF Rule Book, 2026 edition (2025), third print, effective 19 October 2026. USA Shooting applies ISSF rules for these events (its 2025 extract for NCAA quotes the same 6.11.1). | 10 m Air Rifle 60, 10 m Air Pistol 60, 50 m Rifle 3 Positions (indoor, outdoor), 50 m Rifle Prone 60 | 10 m Air Rifle Final (6.17.2) |
| **CMP Air Gun** (`CMP-AG`) | CMP Air Rifle and Air Pistol Competition Rules, 5th edition 2026 (effective 1 March 2026), with the CMP Range Officer scripts "60-shot event on electronic targets" (7 Jan 2025) and "Air gun final on EST" (20 Feb 2026), from https://thecmp.org/competitions/cmp-competitions-rulebooks/ (authoritative for CMP, the user's choice). Standing only. | AR60 | Air Gun Final (10.2) |
| **CMP 3PAR** (`CMP-3PAR`) | CMP National Standard Three-Position Air Rifle Rules, 16th edition, October 2026 to September 2028, with the RO scripts on https://thecmp.org/air/national-standard-three-position-air-rifle-rules/. Also the book NFHS / high-school 3-position air rifle uses. | 3x10, 3x20, 60 standing, 3x20 standing | Standard Final (10.4); Standing Start-from-Zero (10.5, after a standing event); Three-Position Start-from-Zero (10.3, after 3x10 / 3x20) |
| **NRA** (`NRA`) | NRA Precision Air Rifle Rules, 2025. | 3x10, 3x20, 3x40, 40 and 60 standing | NRA Final (7.1.2) |
| **NRA Smallbore** (`NRA-SB`) | NRA Smallbore Rifle Rules, revised February 2026 (competitions.nra.org/media/10575). Electronic targets are authorized for all events (10.10). | 20 courses: conventional 50 ft (A-17) and 50 yd (A-23, A-27), 50 m (A-26); metric prone and position (A-50, A-51, NRA/USAS-50); see below | none (the book has no finals) |
| **No rulebook** (`NONE`) | — | custom only | any built final (each follows its own book) |

The documents themselves are not in this repository (each is its publisher's copyright). The Pi downloads them from
the links in fetcore/rulebooks.py (DOCS, with the SHA-256 of the edition FeT Monitor was built against) and keeps
them, so the book is available without internet; Match Control → Rulebooks shows them and the publishers' pages.
USA Shooting applies the ISSF rules; its current rule extract (2025-26, chapters 6 and 7) is hosted by the NCAA, as
USA Shooting's own site lists only its 2011 rules.

The club shoots its 10 m standing events under the CMP Air Gun book and 3-position under 3PAR (the user,
2026-10-28). The two CMP books number their rules differently (e.g. interruptions: Air Gun 7.12, 3PAR 7.18), so
references are written "CMP AG 7.12.2" and "CMP 3PAR 7.18.3". A reference typed as "CMP 7.x" is looked up in the
match's CMP book.

---

## ISSF

### Who switches the target to record
Range personnel, after "STOP" and a ~30 s pause; then "MATCH FIRING…START" (6.10.4 b, 6.11.1.1 j, 6.11.1.2 a). The
shooter cannot switch early.

### 10 m Air Rifle qualification, 6.11.9.1 and 6.11.1

| Phase | Time | Commands / warnings |
|---|---|---|
| Called to the line | 25 before start (10 set-up) | |
| Preparation and sighting | 15, unlimited sighters | "PREPARATION AND SIGHTING TIME…START"; "30 SECONDS" at 14:30; "END OF PREPARATION AND SIGHTING…STOP" |
| Pause (targets to match) | ~30 s | |
| Match: 60 shots | 75 (1 h 15) | "MATCH FIRING…START"; "10 MINUTES" and "5 MINUTES" remaining; "STOP…UNLOAD" |
| Between relays | 15 break | |

Scoring decimal; top 8 to the Final (qualification scores not carried).

### 10 m Air Rifle Final, 6.17.2 (built in)

| Phase | Time |
|---|---|
| Equipment set-up | from 20 before start |
| "ATHLETES TO THE LINE" | ~10 before start; then "TAKE YOUR POSITIONS", 30 s |
| Preparation and sighting (combined) | 5, unlimited sighters; "30 SECONDS"; "STOP…UNLOAD" |
| Targets cleared for match | 60 s |
| 2 series of 5 shots | 250 s each; "FOR THE FIRST COMPETITION SERIES…LOAD", START 5 s later; STOP at 250 s or when all have fired |
| 14 single shots | 50 s each; "FOR THE NEXT COMPETITION SHOT, LOAD", START 5 s later |
| Eliminations | after shot 12 (8th), 14, 16, 18, 20, 22 (bronze decided), 24 (silver/gold) |
| Ties for elimination | extra single shots until broken |

Start positions by random draw (6.17.1.2); the final starts from zero (6.17.1.5). A finalist who fires out of turn
(not in a shoot-off, or eliminated): the shot is nullified, no penalty (6.17.1.14 m). The ISSF "Ranking Match / Medal
Match" format of the 2023 finals document is not in the 2026 Rule Book and is not used.

How FeT Monitor runs it (competition page, *Finals*): the top n of the class, drawn to the targets (*Draw again*
before the start); officer-paced by default, or spoken (20 s for the announcer's comments before each LOAD, 6.17.2
e). Every series and shot stops at its time or as soon as every finalist still in it has fired. After shots 12-22 the
lowest finalist is out (only places that exist), shot 24 decides gold and silver; a tie at a decision inserts a
one-shot shoot-off (LOAD, 5 s, START, 50 s) for the tied right away, repeated until broken (6.17.2 h).

### 50 m Rifle and 10 m Air Pistol qualifications (6.11.1, 6.11.9.1-3, 7.7)

| Event | Face | Scoring | Phases |
|---|---|---|---|
| 50 m Rifle 3 Positions | 50 m Rifle | whole rings (6.11.9.2) | set-up 10, preparation and sighting 15 (kneeling), pause 30 s, MATCH 60 shots: 20 kneeling, 20 prone, 20 standing (7.7.1) in 1:30 indoor / 1:45 outdoor (7.7.4) |
| 50 m Rifle Prone | 50 m Rifle | decimal (6.11.9.3) | set-up 10, preparation and sighting 15, pause 30 s, MATCH 60 shots in 50 (7.7.4); no final |
| 10 m Air Pistol | 10 m Air Pistol | whole rings (6.11.9.1) | set-up 10, preparation and sighting 15, pause 30 s, MATCH 60 shots in 75 |
| 50 m Pistol | 25 m precision / 50 m pistol (500 mm) | whole rings with inner tens (6.11.9.8) | set-up 10, preparation and sighting 15, pause 30 s, MATCH 60 shots in 1:30; no final |

CMP Air Gun **AP60** (6.4.3 b): as AR60 (call to the line 15, preparation and sighting 15, 60 shots in 75) on the air
pistol face in whole rings; ties 8.5.1 c (inner tens, 10-shot series from the last with their inner tens, shot by
shot), the same countback FeT Monitor uses for CMP whole-ring scoring. Final: the CMP Air Gun Final (6.4.4).

The 25 m pistol events (rapid fire, 25 m pistol, centre fire, standard) are not built: their series are timed by
green and red lights the athlete sees (8.7.6.2 a), and rapid fire needs five targets per athlete (8.7.6.3 a).

Time announced at 10 and 5 minutes (6.11.1.2 e), 30 seconds before the end of sighting (6.11.1.1 i).

**Between positions (7.7.3).** The 3-position MATCH firing is one phase on one clock, in three blocks of 20. After a
position's 20 MATCH shots the athlete switches to sighting (*Back to sighters*, on their own screen, or the range
officer on the console), fires unlimited sighters in the new position (no extra time) and switches back (*Start
record*), which moves the target to the next position. *Back to sighters* is refused in the middle of a position and
after standing has started. A MATCH shot after a position's 20 without that switch is flagged as an extra shot in
that position: ISSF 7.7.3 nullifies it and resets the target to sighting (suggested: annul; then *Back to
sighters*). A pause and resume keeps a lane that is sighting between positions in sighting.

### ISSF finals for these events (built in)

- **10 m Air Pistol Final (6.17.2)**: the same final as 10 m Air Rifle (6.17.2 covers both): 2 x 5 shots in 250 s,
  14 single shots in 50 s, eliminations from shot 12, ties shot off. A final is shot on its qualification's face, so
  after an air pistol event it is the air pistol face, and named so. It follows a standing event only.
- **50 m Rifle 3 Positions Final (6.17.3)**, after a 3-position event: take your positions 30 s; 5 minutes preparation
  and sighting kneeling (30 s); 30 s while the targets are reset to MATCH; 22 minutes for 10 kneeling and 10 prone on
  one clock, with the switch between positions as in the qualification, then sighters for standing in the time left
  (5 minutes and 30 s announced; the 22 minutes run out, no early STOP); 30 s; two 5-shot standing series in 250 s,
  then 8th and 7th are eliminated; single shots 31-35 in 50 s eliminate 6th to 3rd and decide gold and silver. 35
  shots, decimal, starting from zero. Ties at an elimination are shot off, except two finalists tied after the second
  standing series: both are out, ordered by countback (the second standing series, the first, then the last prone
  shot and on backwards, 6.17.3 i).

### ISSF procedures

| Situation | ISSF rule | What FeT Monitor does |
|---|---|---|
| Shot before START of preparation and sighting | 6.11.1.1 h | suggests: miss on the first competition shot |
| Shot after the sighting STOP, before MATCH START | 6.11.1.1 k | suggests: 2 points off the first competition shot |
| Shot after STOP | 6.11.1.3 a | suggests: a miss (sighter: ignore, unfired shots are misses) |
| Sighter after match firing started | 6.11.1.2 d | add a miss by hand |
| Too many shots | 6.11.5 | annul, 2 points off the lowest shot of the first series |
| Target fails: move | 6.10.9.2, 6.11.3.2 | time stops while moving; 5 min extra and unlimited sighters |
| Finish on a later relay | not covered | available; the match director decides (no extra time added) |
| One shooter interrupted | 6.11.3.1-2 | up to 3 min: not given back; over 3: the time lost (last 5 min: time left + 1 min); over 5: + 5 min and sighters |
| Whole range interrupted | 6.10.9.1, 6.11.3.2 | the clock stops; over 5 min: + 5 min and sighters |
| Malfunction | 6.13.2.1, 6.13.4 | no extra time; sighters only if the RO says it is allowable |
| Late arrival | 6.11.4 | joins on a free target, no extra time; sighters within their time |
| Shot does not register | 6.10.9.3; 6.10.9.4 | one more shot, settled after the stage; twice: move |
| Disturbance | 6.11.7 | justified: annulled and refired; not justified: counts |
| Double load | 6.11.2.4 b | the lower shot annulled |
| Crossfire | 6.11.6.1-4 | a record shot is a miss for the firer; a sighter onto a record target: 2 points off the firer's first series (6.11.6.3) |
| Disclaimed shot | 6.11.6.6-8 | annul if the RO confirms it |
| Shot-value protest | 6.16.5.2; not upheld 6.16.5.2 c | before the next shot or within 3 min; not upheld: 2 points off |
| Results protest | 6.16.5.1 | 10 minutes after posting |
| Penalties / DQ | 6.12.6.1 a-c, 6.12.6.3 | rule reference required; DQ needs a second official |
| Incident record | 6.10.4 h | range incident report |
| Ties | 6.15.1 f (decimal), a-d (whole rings) | see Ranking |
| Teams | 3.3.2.3, 6.11.10, 6.12.6.2 g | 3 athletes; a team with a disqualified member is not ranked |

---

## CMP Air Gun (Air Rifle and Air Pistol Rules, 5th ed. 2026)

### AR60 (6.4.1 b, the 60-shot script)
Call to the line at least 15 minutes; 15 minutes preparation and sighting (30 s warning, 7.9.1); then straight on to
60 record shots in 75 minutes (90 on target carriers), warnings at 10 and 5 minutes (7.9.2). Ties (8.5.1 b): last
10-shot series, then the one before ..., then shot by shot from the last. Teams of 2, 3 or 4 as the program says
(6.4.6).

### Air Gun Final (6.4.2, 10.2; built in)
The top 8 by random draw (10.1.2); take positions 30 s (rifle); preparation and sighting 5 min (30 s warning); a pause
of up to 30 s for the targets; 2 series of 5 shots in 250 s; then single shots in 50 s; the lowest is eliminated after
shots 12, 14, 16, 18, 20, 22; shot 24 decides 1st and 2nd. Every tie (an elimination, or 1st/2nd) is shot off (10.3.5,
8.5.2), shot by shot. The final decides the places (10.1.1; a point system is an option). Special regulations
10.3.1-10.3.9: loading before LOAD (warning, then 2 points), a shot between LOAD and START or after STOP is a miss, dry
firing between shots 1 point off the next shot, early STOP (a new 50 s per unfired shot), malfunction (1 minute, then
a new 50 s per unfired shot; once per final), a shot that does not register (protest at once; refire), target failure
(move; 2 minutes of sighters), delay over 5 minutes (2 minutes of sighters for everyone). No "final for all athletes"
in this book.

Note: the rule gives a shoot-off shot 50 s (10.3.5); the Air Gun final script says "after 30 seconds: STOP". The user
chose the rule: 50 s (it ends as soon as the tied finalists have fired).

How FeT Monitor runs it (competition page, *Finals*): the top n of the class (n up to 8, at most one per target) drawn
to the targets at random (*Draw again* before the start); officer-paced by default (Next for each LOAD), or spoken
(20 s for the scores before each LOAD). Series and single shots stop at their time. After shots 12-22 the lowest
finalist is out (only places that exist: with fewer finalists the first eliminations are skipped); after shot 24 1st
and 2nd are decided. A tie at a decision inserts a one-shot shoot-off for the tied finalists at once (50 s, ends when
they have fired), repeated until broken; if shoot-offs never break it (the format's phase limit), the place is left
for the match director and logged. Eliminated targets stay in sighting; their later shots are flagged.

### CMP Air Gun procedures

| Situation | Air Gun rule | What FeT Monitor does |
|---|---|---|
| Shot before START of preparation and sighting | 7.11.1 | disqualification: ignore the shot, then *Disqualify…* (a second official) |
| Shot after the sighting STOP, before match START | 7.11.2 | suggests: a miss on the first match shot |
| Shot after STOP at the end of record fire | 7.11.3 | a miss; with electronic targets the last record shot is deleted: suggests annul / ignore |
| Sighter during record fire | not covered | the match director decides |
| Shot while the range is stopped (emergency STOP-STOP-STOP, 2.13) | not covered | the match director decides |
| Too many shots | 7.11.8 | annul the last shots, 2 points off the lowest shot of the first 10-shot series |
| Shots not fired in time | 7.11.6 | misses (the count shows them) |
| Target fails: move, or resquadded to a later relay | 7.12, 7.12.2 | the time left + 2 min and unlimited sighters (both) |
| One shooter interrupted | 7.12.1-2 | up to 1 min: not given back; over 1 min: the time lost; over 5 min: + 2 min and sighters |
| Whole range interrupted | 7.12.3 | the clock stops; over 5 min: + 2 min and sighters |
| Malfunction | 7.13 | sighters within the time left; + 2 min if in the last 5 minutes |
| Late arrival | 7.14 | starts if time remains, no extra time |
| Shot does not register | 8.2.3 a | one more shot, settled after the stage; twice: move |
| Disturbance | not covered | available; the match director decides |
| Double load | 7.11.4 b | the higher counts, the second annulled |
| Crossfire | 7.11.9 | a record shot is a miss for the firer; a sighter onto another's sighting target: no penalty; onto a record target: not covered |
| Disclaimed shot | 7.11.10 | counts unless the officials confirm another fired it |
| Shot-value protest | 8.2.3 (c: not upheld) | before the next shot or within 3 min; not upheld: 2 points off |
| Results protest | 8.1.9 | 10 minutes after posting (electronic targets) |
| Penalties / DQ | 9.2 a-c | warning, 2 (or 5) points, DQ: penalty and DQ by at least two officials |
| Incident record | 7.11 (Range Incident Form, RIF) | range incident report |
| Ties | 8.5.1 b (EST air rifle); 8.5.2 finals | as 3PAR decimal (same countback) |
| Teams | 6.4.6, 8.5.4 | 2, 3 or 4 as the program says (default 4) |

---

## CMP 3PAR (National Standard Three-Position Air Rifle Rules 2026-2028)

### Who switches the target to record
After the sighting stage "STOP", a pause to switch the targets to Match Fire, then "…MATCH FIRE…START" (7.8, 7.9,
7.11). No early switch; no sighters in record stages (7.15.1).

### 3x10 (5.3.2) and 3x20 (5.3.3), kneeling-prone-standing order

| Stage | 3x10 | 3x20 | Warnings |
|---|---|---|---|
| Call to line | ≥5 before P&S (5.3.1 b, 7.6) | same | |
| Preparation and sighting, kneeling | 8 | 8 | 30 s |
| Pause, then kneeling record | 10 (10 shots) | 20 (20 shots) | 5 and 2 min |
| Changeover kneeling to prone | 5 | 5 | |
| Sighting, prone | 5 | 5 | 30 s |
| Pause, then prone record | 10 | 20 | 5 and 2 min |
| Changeover prone to standing | 5 | 5 | |
| Sighting, standing | 5 | 5 | 30 s |
| Pause, then standing record | 15 | 25 | 5 and 2 min |

5.3.1 d: for inexperienced 3x10 athletes the Match Director may lengthen the first P&S to 15 or 20 minutes.
Scoring: whole rings, or tenths with electronic targets as the program announces (5.3.1 e).

### Standing (5.3.4)

| Event | Stages |
|---|---|
| 3x20 standing | take your positions (≥5); P&S 8; record 20 shots 25; then twice: targets changed, "TAKE YOUR POSITIONS" 30 s, standing sighting 5, record 20 shots 25 (the 60-shot script) |
| 60 standing | P&S 15; record 60 shots 75 (with electronic targets the 60-shot event "may be fired without changeover periods") |

40-shot standing events are mentioned in 5.3.1 e (scoring) but have no table in 5.3.4: to confirm.

### 3PAR finals (Rule 10; all three built in)

The top 8 of the qualification event (3x10, 3x20, 40 or 60 standing) fire a Final on 8 adjacent firing points,
labelled A-H (10.2.2, 10.2.3); decimal scoring (10.2.8); a count-down clock per shot (10.2.2).

| Final | Course of fire | Firing points | Ranking | Ties |
|---|---|---|---|---|
| Standard (10.4), after any event | 2 min to take positions; 5 min preparation and sighting (30 s warning); then 10 shots standing, each on command: LOAD, 5 s, START, 50 s or all fired, STOP | by qualification rank, best on point A (10.4.1) | qualification + final total (10.4.3) | places 1-3: one-shot shoot-offs, 50 s (10.2.9 n); 4-7: final score first, then 8.6.1 (8.6.2) |
| Standing Start-from-Zero (10.5), after 60 standing | 5 min preparation and sighting; 2 series of 5 shots in 250 s; then single shots in 50 s; after shot 12 the 8th place is eliminated, then one more every 2 shots; shot 24 decides 1st/2nd | random draw | the final only | elimination ties: one-shot shoot-offs until broken (10.5.4 f, 10.2.9 n) |
| Three-position Start-from-Zero (10.3), after 3x10 or 3x20 | 30 s preparation; 5 min sighting kneeling; 10 shots kneeling in 400 s; 7 min changeover and sighting; 10 prone in 300 s; 7 min changeover and sighting; 5 + 5 standing in 250 s each (then 8th and 7th eliminated); single shots 31-35 in 50 s, eliminating 6th, 5th, 4th, 3rd; shot 35 decides 1st/2nd | random draw (10.3.1) | the final only | as above |

Rules for every 3PAR final (10.2.9): a shot before LOAD/START or after STOP is a miss for that shot; discharge before
the preparation time: 2 points off the first final shot; an early STOP gives the athlete a new full time for that
shot; a malfunction not their fault: 1 minute to repair, then a new full time (one per final); a target failure:
move, 2 minutes of sighters, then the missing shot(s) while the others wait; all targets fail: qualification plus the
final shots fired so far; a score protest immediately, before the next shot's commands. A finalist who does not
appear is last (10.2.4). Options (10.1.2): a Standard Final for everyone at the end of each relay (only the top 8 of
the qualification count), daily finals, or a points system for aggregates.

How FeT Monitor runs the Standard Final (competition page, *Finals*): choose the class, the targets (in order: A, B,
C ...) and the number of finalists (default: one per target, at most 8; fewer than 8 when the range has fewer
targets); the top qualifiers are placed best on A. Or, after a relay finishes, *Final for relay N* (10.1.2 a):
everyone of that relay on their own target; the standings of all relay finals together count only the top 8
qualifiers of each class (the rest are listed for information). Officer-paced by default: the range officer presses
Next for LOAD, START follows 5 seconds later, and the shot ends after 50 seconds or as soon as every finalist has
fired. Ties for places 1-3: the console offers a one-shot shoot-off for the tied firing points (50 s, repeated until
broken); places 4-7: the final score, then the final's shots by 8.6.1 e. The final never counts in the qualification
results.

Note: 10.3.2 c says "400/300/150 seconds" for the series, but the course of fire in 10.3.1 gives 250 s for each
5-shot standing series; 250 s is used.

How FeT Monitor runs the Start-from-Zero finals (competition page, *Finals*; only after the event they belong to: the
three-position one after an event with kneeling and prone, the standing one after a standing event): the top n of
the class drawn to the targets (*Draw again* before the start); the commands of the CMP scripts ("ISSF Standing
Final", "ISSF Three-Position Final"): standing, TAKE YOUR POSITIONS 2 minutes; three-position, 30 seconds and a
**5-minute** kneeling-to-prone changeover (the script; 10.3.1 prints "5 7-minutes"), 7 minutes prone-to-standing;
30-second warnings in every series and changeover (script). Every series and shot stops at its time or when every
finalist still in it has fired (10.3.2 c, 10.5.3 c). After the second standing series of the three-position final the
two lowest are out together (8th, then 7th); a tie at any decision, also between the two going out, inserts a
one-shot shoot-off for the tied right away (10.2.9 n). Spoken: 20 s for the comments before each LOAD (10.3.2 d,
10.5.3 d).

### CMP 3PAR procedures

| Situation | 3PAR rule | What FeT Monitor does |
|---|---|---|
| Shot before START of a sighting stage | 7.17.1 | suggests: a miss on the first competition shot |
| Shot after the sighting STOP, before match START | 7.17.2 | suggests: 2 points off the first competition shot |
| Shot after the STOP of a match fire stage | 7.17.2 | a miss on the last competition shot (by hand during a changeover) |
| Sighter during match fire, not authorized | 7.15.1 | add a miss by hand |
| Shot after an emergency STOP | 7.17.3 | counts, with a warning |
| Too many shots in a position | 7.17.8 a | annul, 2 points off the lowest shot |
| Target fails: move, or finish on a later relay | 7.18, 7.18.3 | the time left + 2 min and unlimited sighters (both) |
| One shooter interrupted | 7.18.1-2 | the time lost; over 5 min: + 2 min and sighters |
| Whole range interrupted | 7.18.4 | the clock stops; over 5 min: + 2 min and sighters |
| Malfunction | 7.19.1 | sighters on request; + 2 min after the 5-minute warning |
| Late arrival | 7.20 | starts, no extra time; sighters may be authorized |
| Shot does not register | 8.4 a | one more shot, settled after the stage; twice: move |
| Disturbance | not covered | available; the match director decides |
| Double load | 7.17.11 (7.19.3) | the higher counts, the lower annulled |
| Crossfire | 7.17.9 | a record shot is a miss for the firer; a sighter onto another's sighting target: no penalty; onto a record target: not covered |
| Disclaimed shot | 7.17.10 | annulled if the RO confirms it; otherwise it counts |
| Shot-value protest | 8.4 (c: not upheld) | before the next shot or within 3 min; not upheld: 2 points off |
| Results protest | 8.2 | 10 minutes after posting (electronic targets) |
| Penalties / DQ | 7.22.1-3 | warning, 2 points, DQ by at least two officials |
| Incident record | 6.5 (Range Incident Form) | range incident report |
| Ties | 8.6.1 a-e; finals 8.6.2, 10.2.9 n | see Ranking |
| Teams | 3.2, 3.2.2 c, 8.6.4 | 4 athletes; Best-Four-Count teams optional |

---

## NRA (Precision Air Rifle Rules 2025)

### Formats (7.1, 8.2), prone-standing-kneeling order
Sighting: 8 for the first set of targets or position, 5 for each one after. Record: 1 minute per shot prone and
kneeling; standing 10 shots 15, 20 shots 25, 40 shots 50, 60 shots 75. Warnings at 5 and 2 minutes (8.4). Position
change: at least 5 minutes, 15 recommended (8.2 b). Unused time does not carry over (8.1). Sighters "at any time after
START within the time limit for that match or stage"; once the first record shot of a position is fired, no more
sighters (2-point penalty each, 9.2 d): the shooter switches to record.

| Event | Order and record times |
|---|---|
| 3x10 | prone 10, standing 15, kneeling 10 (each after its sighting period) |
| 3x20 | prone 20, standing 25, kneeling 20 |
| 3x40 | prone 40, standing 50, kneeling 40 |
| 40 standing / 60 standing | 50 / 75 |

NRA Sporter (separate book, no printed year) uses the same time table and also 4-position courses: to confirm
against a dated edition before building.

### NRA procedures

| Situation | NRA rule | What FeT Monitor does |
|---|---|---|
| Early or late shots | 14.5 | the highest-value shots become misses (by hand) |
| Sighter after the first record shot | 9.2 d | suggests: 2 points off |
| Too many hits | 10.10.4 d | annul the highest-value hit |
| Target fails: move | 10.10.5 c, 9.2 b | time stops while moving; 5 min extra and unlimited sighters |
| Finish on a later relay | not covered | available; the match director decides (no extra time added) |
| One shooter interrupted | 9.2 b | over 3 min: equal time; over 5 min: sighters |
| Whole range interrupted | 10.3.2 (9.2 b) | the clock stops; over 5 min: sighters. All targets failed (10.10.5 b): + 5 min, use *Extra time* |
| Disabled rifle | 9.5, 9.2 c | the shooter's time stops until *Repaired*, then the time that remained and sighters |
| Late arrival | not covered | available; the match director decides |
| Shot does not register | 10.10.5 d; twice 10.10.5 d 3 | one more shot, settled after the relay; twice: move (5 min, sighters, both shots refired) |
| Disturbance, double load | not covered | available; the match director decides. Shots within 3 s of another report, with disagreeing microphones, or heard but not placed are flagged |
| Crossfire | 10.10.4 a-c | a record shot is a miss; a sighter onto a record target: 2 points off, first series |
| Disclaimed shot | 10.10.4 e-f | annul if the RO confirms it |
| Shot-value protest not upheld | not covered | no penalty suggested |
| Results challenge | 16.1 b | the challenge time set for the competition (30 min unless changed) |
| Penalties / DQ | 9.30 | warning, deduction, DQ |
| Incident record | Range Officer's Notation | range incident report |
| Ties | 15.3 | see Ranking |
| Teams | 2.10-2.15, 12.3 | size set by the match program (4 used as the default) |

---

### NRA Final (7.1.2; built in)

| Phase | Time |
|---|---|
| Preparation period | 3 (d) |
| Sighting | 7, unlimited sighters on the sighter bulls; 30 s warning (e, f) |
| Pause | 30 s (g) |
| 10 shots standing | each: "FOR THE FIRST (NEXT) COMPETITION SHOT, LOAD"; "ATTENTION - 3 2 1"; "START", 75 s; "STOP" after the last finalist has fired or at 75 s (m); each shot scored and announced before the next (n) |
| Ranking | qualification + final (o); the final is scored in tenths (o), also after a whole-ring qualification |
| Ties | qualification + final tied: single shots by the same procedure until broken (q), for any place |
| Start positions | firing points 1-8 by qualification rank (k) |

How FeT Monitor runs it: the top n of the class on the targets in qualification order; officer-paced or spoken (20 s
for the scores before each LOAD, n); after the last shot the console offers a shoot-off for every tie (LOAD,
ATTENTION 3 2 1, START, 75 s). A finalist who does not report is placed last (i): the match director leaves them out.

## NRA Smallbore (Smallbore Rifle Rules, revised February 2026)

Each course is scored in whole rings with X's (center shots on targets without an X ring) on its own face
(fetcore/faces.py, the dimensions of 4.2-4.10, the .224 in gauge of 14.3 a). Every course starts with a 3-minute
preparation period (10.3.1). Sighters are allowed at any time (9.2): each stage is one clock, sighters until the
shooter starts record. No time warnings are given (8.4). The commands are 10.7's ("Load. Commence firing." /
"Cease firing. Bolts open, insert your ECIs.").

| Course (rule) | Face | Positions (order as the rule's table lists them; the program may change it, 5) | Time per record shot |
|---|---|---|---|
| 50 ft Conventional 4-Position 4x10, 4x20 (7.3) | A-17 | prone, sitting, kneeling, standing | 1 min; standing 1 1/2 (8.2 a) |
| 50 ft Conventional 3-Position 3x10, 3x20 (7.3, sitting left out) | A-17 | prone, kneeling, standing | as above |
| 50 yd Conventional Prone 20 (7.2.1), 40 (7.2) | A-23 | prone | 1 min; a 40-shot stage on e-targets 45 min (8.1.1) |
| 50 yd Conventional 4-Position 4x10, 4x20 (7.3) | A-23 | prone, sitting, kneeling, standing | 1 min prone; 1 1/2 the others (8.2 b) |
| 50 yd / 50 m Conventional Prone 40 (7.2) | A-27 / A-26 | prone | 45 min (8.1.1) |
| 50 m / 50 yd Metric Prone 40, 60 (7.4) | A-50 (= ISSF 50 m) / A-51 | prone | 45 / 65 min (8.1.1) |
| NRA Metric Position 3x20, 3x40 at 50 ft, 50 m, 50 yd (7.5) | NRA/USAS-50, A-50, A-51 | prone, standing, kneeling | 1 min prone, 1 1/2 kneeling, 2 standing (8.2 c) |

Position change: 5 minutes (8.2: at least 5 more minutes for each). Team size: 4 by default (the program decides).

### NRA Smallbore procedures

| Situation | NRA SB rule | What FeT Monitor does |
|---|---|---|
| Early or late shots | 14.5 | the highest-value shots become misses (by hand) |
| Sighter during record | 9.2 | allowed: *Back to sighters* (the shooter on their screen, or the console) switches the lane to sighters; *Start record* goes on with record where it was. No penalty, no flag |
| Too many hits | 10.10.4 d | annul the highest-value hit |
| Target fails: move | 10.10.5 c | time stops while moving; 5 min extra and unlimited sighters |
| One shooter or the range interrupted | 10.3.2 | the clock stops; 2 minutes or more: 1 minute more and sighters. All targets failed (10.10.5 b): + 5 min, use *Extra time* |
| Disabled rifle | 9.5 a, 10.9 | the shooter's time stops until *Repaired*, then the time that remained in the stage, and sighters |
| Shot does not register | 10.10.5 d; twice 10.10.5 d 3 | one more shot, settled after the relay; twice: move (5 min, sighters) |
| Crossfire | 10.10.4 a-c | a record shot is a miss; a sighter onto a record target: **1 point** off, first series |
| Disclaimed shot | 10.10.4 e-f | annul if the RO confirms it |
| Results challenge | 16.1 | the challenge time set for the competition |
| Penalties / DQ | 9.30 | warning, deduction, DQ |
| Ties | 15.7 | see Ranking: X's, stages from the last, bulls from the last, X's of higher value; positions: standing, kneeling, prone |
| Finals, later relay, late arrival, double load, disturbance | not covered | available; the match director decides |

## No rulebook

A custom format may say "No rulebook". Nothing is cited and nothing is suggested: every flagged shot shows "the match
director decides". A move or an interruption gives back the time lost and nothing more (add *Extra time* if the
director gives it); a malfunction asks whether sighters are allowed; the results protest time is the challenge time
set for the competition. The director picks the tie-break method (ISSF, CMP or NRA). Any built final can follow such
a competition; the final then runs by its own book.

---

## How FeT Monitor does it (the same for every book; the rules above decide the numbers)

### Lanes (0.7.17)
- every target has a lane number on the range (Range Control → Targets), each used once; a target without one is a
  spare: it cannot be entered in a match;
- a match's firing points are its targets' lanes; a shooter keeps the lane number they joined with as their key for
  corrections and shoot-offs, and the screens show the lane they are on now: moved onto another lane's target, that
  lane; moved onto a spare put on the same stand, the same lane;
- finals: position A is the lowest lane of the final, then B, C ...;
- matches from before lanes keep their old numbers (the order within the match).

### Moving a shooter to another target (*Move…*)
- the shooter's series goes with them to the new target, so their shots so far stay theirs; the old target leaves
  the match (a shot fired on it before the move still counts, even if it arrives later);
- their own clock stops at the move (the time lost is given back) and runs again when the range officer presses
  *Shooter ready*, with the book's extra time prefilled and changeable;
- they are in sighting until *Start record* (console, or the shooter's own screen); a sighter after that is flagged;
- when the match moves on before their time is up, they finish their phase on their own clock; a match whose time is
  over while someone is finishing shows "Time over: moved shooters are finishing";
- shots on the new target while they are moving are flagged ("while moving to another target");
- before the start, *Move…* only changes the target; in a competition, the competitor window (pencil) places a
  competitor on any relay that has not started, or swaps two competitors.
- *Finish on a later relay* (competitions): the shots so far stay on this relay; on the later relay the shooter waits
  in sighting until the stage they left, then has the time they had left plus the book's extra time (the CMP books:
  2 minutes; others: none, the director decides), unlimited sighters, and only the record shots still owed; then the
  rest of the match with everyone. The results join both parts ("1 · 3 → 2 · 1").

### Shots that may not be one clean shot (double load, echo, noise; 2026-10-31)
Seen on a freETarget board at a low trip point: the paper drive, an echo or room noise heard as a shot; two pellets
from one loading heard as one shot whose microphone times disagree, or as two reports milliseconds apart.
- a shot fired in its time is flagged "*n* s after shot *m*: a double load, an echo or noise?" when another report on
  the same firing point came at most 3 s before it (`matches.CLOSE_MS`: no air rifle or air pistol is loaded and
  fired again that fast), and "the four microphones disagree by *x* mm" when the four times miss one point by more
  than 2 mm (`MISFIT_FLAG_UM`; a good shot: about 0.4 mm). Nothing is suggested: the director looks at the target.
  Undecided, it does not count and does not use up a record shot; decided *Count*, it counts (and can make a later
  shot one too many). The flag offers *Double load…*: the existing correction, with the other shot already chosen;
- a shot the target heard but could not place (freETarget sends `"x":NAN`; the FeT target firmware's times that fit
  no point) is kept apart from the shots (table `unplaced`, migration 20) and counts as nothing. In a match the
  director decides *Not a shot* (counts nothing) or *A shot: scored as a miss*; undecided, the results cannot be made
  official. Before migration 20 such lines were refused as unreadable: `python3 -m fetserver rescore` recovers them
  (and the misfits of older freETarget shots);
- in practice nothing is decided: the shot list marks such shots with a red **?** (the reason on hover, and under the
  last shot) and counts "heard, not placed" reports below the list; totals are what the target sent.

### "You may discharge air downrange" (0.7.16)
Once a relay has ended, the range officer's console offers this command (spoken when the console's sound is on). A
shot fired from then on, such as air being discharged or a pellet left in the bore, never counts and is not flagged.
A shot after the end but before the command is flagged for the match director as always (the rulebook decides). A
restart reopens the match and cancels the command. No rulebook here names the command: it is a range convention, and
the same rule applies to remote matches on deeptens (docs/PROTOCOL.md, version 2).

### Corrections (*Shots…*; fetserver/corrections.py)
- range officers mark a shot *under review*, record a *protest*, a *disturbance* or a shot that *did not register*;
  the match director settles them; a protest of a shot value that is not upheld suggests 2 points off where the book
  says so, unless the shot was a zero;
- the match director can override a shot's value (the fired value stays on record), annul or add a shot, record a
  crossfire (from a firing point, or unknown), a double load, give a penalty (a rule reference is required) or
  disqualify (a rule reference and a second official);
- a penalty without a shot comes off the first series (its best shot, so the whole deduction applies);
- *too many shots* in one step: "Annul, 2 points off" annuls the extra shot and takes 2 points off the lowest shot of
  the first series;
- *did not register*: one more match shot; after the stage the director settles it: found (its value is entered and
  counts) or not found (a miss); either way the last shot of that stage, the extra one, is annulled; marked twice,
  the console offers Move;
- nothing is deleted: every correction is kept with who and when, and can be voided;
- rule references are explained in plain words on the results and score cards (fetcore/rules.py), looked up in
  the match's book unless the reference names another.

### Official results and decision guidance
- Results are *provisional* until the match director makes them *official*: only when the match finished, every
  flagged shot is decided and every review or protest is settled. Official results cannot be changed until reopened
  (logged). A competition is official when every relay that was not stopped is.
- Each flagged shot shows the rule of the match's book, a plain summary, a link to that book and the suggested
  decision, or what to do by hand. The suggestion is never applied by itself.
- *Post preliminary results* (match director) starts the book's protest time; results pages and TV boards show
  "protests until hh:mm"; *Make official* waits for it; the director can override (logged).
- *Range incident report* (console, Results panel): every flag and decision, correction (also voided ones), move,
  relay change, pause, interruption, malfunction, late arrival, extra time, restart, posting and official/reopen, in
  time order with who did it and the rule; printable with signature lines, and as CSV.
- Sighters granted by an interruption, malfunction or late arrival are allowed until the shooter (or the range
  officer) presses *Start record*.

### How the formats are built in (fetcore/matchformats.py)
Each table above is one format; each phase carries its rule number and the book's spoken commands. The "pause"
between sighting and record is 30 s (ISSF 6.11.1.1 j says about 30 s; the CMP books say a pause is needed without a
length: 30 s is our choice). NRA 3-position: one clock per position (sighting time + record time, 8.1) and a
15-minute position change (8.2 b, recommended; at least 5).

### Ranking and tie-breaking (fetcore/ranking.py)

| Book | Rule | Order |
|---|---|---|
| ISSF, decimal (10 m air rifle) | 6.15.1 f | total; last 10-shot series backwards (decimal); shot by shot backwards (decimal); then equal rank, listed alphabetically by family name (e) |
| ISSF, whole rings | 6.15.1 a-d | total; inner tens; last 10-shot series backwards; shot by shot backwards with an inner ten above a ten; shot by shot in decimals (EST) |
| CMP Air Gun, EST air rifle | 8.5.1 b | last 10-shot series backwards, then shot by shot from the last: the same countback as 3PAR decimal |
| CMP 3PAR, decimal | 8.6.1 e | as ISSF decimal (inner tens are not used with decimals, 8.6.1 a) |
| CMP 3PAR, whole rings | 8.6.1 a-d | total; inner tens; 10-shot series backwards (score, then that series' inner tens); shot by shot backwards, inner ten above a ten; then duplicate awards |
| NRA | 15.3 | total; last 10-shot string backwards; most 10s, then 9s, 8s ...; most inner 10s; then equal rank alphabetically |
| NRA Smallbore, one position | 15.7.1 a, 15.7.2 a | total; X's (center shots); each stage from the last (score, then X's); each bull from the last (prone: its score; other positions: score, then X's); X's of higher value (14.3 e, g) and the stages again with them; then tied (15.17: the match director decides) |
| NRA Smallbore, several positions | 15.7.2 b | total; X's; standing, kneeling, prone totals (with X's); stages from the last, then bulls from the last, standing first, then kneeling, sitting, prone |

Smallbore stages are the format's record phases in order, and bulls are runs of the face's shots per bull (7.1:
1 at 50 ft, 5 at 50 yd / m). An X of higher value is an X whose hole clears the X ring's edge (14.3 e); on the A-17
a center shot of higher value covers the whole ten ring (14.3 g). Both are decided from the shot's position.

Teams (ISSF 6.15.5, CMP AG 8.5.4, CMP 3PAR 8.6.4, NRA 15.7): the members' results totalled and ranked like one
competitor (countback lists added position by position). The built-in team size follows the book and can be changed
per competition; "best count" is optional.

## Finals on the TV board
A scoreboard *TV display* (Range Control → TV displays) that follows the competition switches to its final as soon as
the final is set up (or *TV board for this final* on a final's console). It shows a running final: the finalists in
their current places, everyone's last shot, the final (and for the added finals the qualification and total), shot
k of n fired, which place is decided next and after which shot, who is in a shoot-off and which decision it settles,
eliminated finalists greyed with their place, and the medals as they are decided.

## Still to build
Nothing from the rulebook finals list. NRA Sporter Air Rifle (separate book) when needed.
NRA Smallbore: the Dewar course (20 shots at 50 yd, then 20 at 100 yd; ties 15.7.1 a 2) and the 100 yd faces
(A-25, A-33): very low priority (the user, 2026-11-04: hard to run properly in the real world with one target per lane).

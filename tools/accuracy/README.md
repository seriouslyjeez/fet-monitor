# Accuracy testing with frame cards

1. Print cards: `python3 tools/aiming_sheet.py card` (dist/aiming-card.pdf); cut out, mount in the target frame in
   place of the target card, witness paper as normal.
2. Shoot the marks in order (1 centre, 2-7 clockwise from 12 o'clock). After EACH shot take the card out, photograph it
   flat from straight above with all twelve reference dots in view, put it back.
3. Copy the server's database (Range Control backup, or `/var/lib/fet-monitor/fet.db` with the service stopped).
4. Measure the holes (needs numpy, opencv-python, pillow, pillow-heif):
   `tools/accuracy/measure.py OUT IMG_1.HEIC ... IMG_6.HEIC NEWCARD IMG_7.HEIC ...`
   then look at every `OUT/*_rect.png` (green: what opened; cyan: the fitted pellet hole).
5. Compare: `tools/accuracy/report.py DB OUT/holes.json --device <id> --series <n> --cards 55-60 61-66`
   (a bounce or a shot without a photo: leave its number out).

Keep every test's photos, database copy and results in its own dated folder (outside git; ~/workshop/fet-testing).
Baseline 2026-10-31 (firmware 4.3.0, V_REF 4.93 V, out of spec): median 5.7 mm, max 8.4 mm.

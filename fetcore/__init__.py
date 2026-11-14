"""fetcore: the scoring brain shared by FeT Monitor (the range server) and deeptens.com (the online service).

Pure Python, standard library only, no database and no network: the shot solver (shotsolve), target faces and exact
scoring (faces, scoring), shot-line parsing (lineparse for the original freETarget firmware, targetproto for FeT target
firmware), match formats and rulebooks (matchformats, rulebooks, rules), tie-breaks (ranking), group statistics
(groupstats), sight adjustment (sightadj), and the match engine (matchengine: timeline, clock, flags, counted shots;
matchrun: the commands). One scoring standard: a remote match records the fetcore version it was
scored with.

MIT License, like FeT Monitor. deeptens pins fetcore to a tagged fet-monitor release.

What places and scores a shot gives the same results to the bit on every machine (0.3.0): the Pi, deepTENS and the
CYD display, which runs it on MicroPython (docs/PLAN-cyd.md). In those modules (exact, fmath, shotsolve, faces,
scoring, lineparse, targetproto): fmath for exp, log, sin, cos, atan2, acos, hypot (not math: C libraries differ);
exact for numbers from text (not float() or decimal); squares as x * x (not ** 2); long float constants as bits;
plain classes (no dataclasses); no reliance on dict order; no regular expressions beyond MicroPython's.
tests/test_micropython.py checks it. The others (groupstats, sightadj, the match engine) are not ported yet.
"""
__version__ = "0.3.0"   # 0.3.0 (2026-11-13): the same bits on every machine (fmath, exact); runs on MicroPython

"""fetcore: the scoring brain shared by FeT Monitor (the range server) and deeptens.com (the online service).

Pure Python, standard library only, no database and no network: the shot solver (shotsolve), target faces and exact
scoring (faces, scoring), shot-line parsing (lineparse for the original freETarget firmware, targetproto for FeT target
firmware), match formats and rulebooks (matchformats, rulebooks, rules), tie-breaks (ranking), group statistics
(groupstats), sight adjustment (sightadj), and the match engine (matchengine: timeline, clock, flags, counted shots;
matchrun: the commands). One scoring standard: a remote match records the fetcore version it was
scored with.

MIT License, like FeT Monitor. deeptens pins fetcore to a tagged fet-monitor release.
"""
__version__ = "0.2.0"

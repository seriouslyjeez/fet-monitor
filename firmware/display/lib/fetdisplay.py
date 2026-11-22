# FeT Display's version, in a module of its own: the flash station (fetserver/flashstation.py) imports it over USB to
# learn what a display runs without starting the display (importing app starts it). MARKER is searched for in the
# firmware image by tools/release.py, fetserver/displayfw.py and deepTENS.
VERSION = "0.2.2"
MARKER = "fet-display/0.2.2"

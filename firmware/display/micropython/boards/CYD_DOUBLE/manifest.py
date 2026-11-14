# FeT Display's code frozen into the firmware: it runs from flash instead of being loaded into memory (with it all in
# /lib, a portable kit ran out of memory on the CYD, 2026-11-14). fetcore's portable modules are the repository's own
# files, frozen as they are: one scoring codebase (tests/test_micropython.py: the same bits as on the Pi and deepTENS).
include("$(PORT_DIR)/boards/manifest.py")
freeze("$(BOARD_DIR)/../../../lib")
freeze("$(BOARD_DIR)/../../..", "main.py")
package("fetcore", files=("__init__.py", "exact.py", "faces.py", "fmath.py", "lineparse.py", "scoring.py", "shotsolve.py",
                          "targetproto.py"), base_path="$(BOARD_DIR)/../../../../..")

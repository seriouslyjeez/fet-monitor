#!/bin/sh
# Rebuild the printable STLs from source/FET_pipe_guide.scad (needs OpenSCAD on PATH).
set -e
cd "$(dirname "$0")"
S=source/FET_pipe_guide.scad
build() { openscad --export-format binstl -D NUT_TRAP=$2 -D ONE_PIECE=$3 -o "$1" "$S"; }
build captured-bolt/FET_pipe_guide_captured_bolt_print_x4.stl          false false
build captured-bolt/FET_pipe_guide_captured_bolt_onepiece_print_x2.stl false true
build captured-nut/FET_pipe_guide_captured_nut_print_x4.stl            true  false
build captured-nut/FET_pipe_guide_captured_nut_onepiece_print_x2.stl   true  true
ls -lh */*.stl

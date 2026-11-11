#!/bin/sh
# Rebuild the printable STLs from source/FET_rifle_mask_holder.scad (needs OpenSCAD on PATH).
set -e
cd "$(dirname "$0")"
S=source/FET_rifle_mask_holder.scad
build() { openscad --export-format binstl -D "PIECE=\"$2\"" -o "$1" "$S"; }
build onepiece/FET_rifle_mask_holder_onepiece_print_x1.stl      whole
build split-3-piece/FET_rifle_mask_holder_crossbar_print_x1.stl bar
build split-3-piece/FET_rifle_mask_holder_left_leg_print_x1.stl left
build split-3-piece/FET_rifle_mask_holder_right_leg_print_x1.stl right
ls -lh */*.stl

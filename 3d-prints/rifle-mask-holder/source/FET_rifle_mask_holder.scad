// freETarget rifle mask holder for a 185 x 229 mm (7.25" x 9") paper mask.
// The mask's 30.5 mm hole sits concentric with the faceplate hole; the witness paper
// runs top to bottom between the mask and the faceplate and leaves under the crossbar.
// The legs stop just above the top screws, so a mask can be fed in from the top.
// Mounts on the faceplate's 4 screws (Rifle Face Plate.dxf / FET-016: dia 5 mm holes at
// x = +-100, y = +74.5 / -75.5 from the hole centre). Pocket and notch copy
// "Rifle Target Holder v17": 3 mm thick, 2 mm pocket, 98 x 1 mm witness-paper notch.
// Two arrows recessed in the front face mark the horizontal centre line of the mask hole.
//
// Axes: origin = faceplate hole centre, X right, Y up (crossbar at the bottom),
//       Z = 0 on the back face (against the faceplate), Z = T on the front.
// Build the STLs with ../build-stls.sh.

PIECE        = "whole";   // "whole" (one piece), "bar", "left", "right", "assembly"
PRINT_LAYOUT = true;      // front face down on the bed

// ---- paper mask ----
PAPER_W  = 185;
PAPER_H  = 229;
HOLE_D   = 30.5;
HOLE_TOP = 89;                       // top of the mask hole, from the top of the paper
AIM_FROM_TOP = HOLE_TOP + HOLE_D/2;  // 104.25

// ---- pocket and notch (same as Rifle Target Holder v17) ----
T         = 3;      // overall thickness
POCKET_D  = 2;      // mask + witness paper pocket, from the back
PAPER_CLR = 1;      // pocket clearance around the mask, each side
NOTCH_W   = 98;     // witness-paper notch under the crossbar
NOTCH_D   = 1;

// ---- frame ----
LIP        = 5;     // frame overlaps the mask edge by this much (sides and bottom)
BAR_BELOW  = 2;     // full-thickness strip below the pocket (was 12; 10 mm removed so the
                    // crossbar clears the paper-feed frame)
HEAD_D     = 9.5;   // screw head diameter (M5 pan head, 3 mm hex socket)
TOP_EDGE   = 3;     // plastic left above the screw head; the legs stop here so a mask can be
                    // fed in from the top past the target's paper guide (the mask top is not held)
OUTER_X    = 108;   // half width

// ---- faceplate screws ----
BOLT_X    = 100;
BOLT_Y    = 75;     // slot centre; the 1 mm slot covers +74.5 / -75.5 either way up
BOLT_SLOT = 1;
BOLT_D    = 5.5;    // M5 clearance (3 mm hex socket pan heads); was 5.0

// ---- centre-line arrows (front face) ----
ARROW_DEPTH = 0.6;  // recessed: 3 layers at 0.2 mm
ARROW_TIP   = 1;    // tip this far outside the pocket edge (full-thickness part of the leg)
ARROW_HEAD_L = 6;
ARROW_HEAD_W = 7;
ARROW_TAIL_L = 5.5;
ARROW_TAIL_W = 2.4;

// ---- split version ----
SPLIT_Y  = -60;     // legs and crossbar part here, between the screws
DT_NECK  = 4.5;     // dovetail neck width
DT_HEAD  = 7;       // dovetail head width
DT_DEPTH = 6;       // how far the tongue reaches into the leg
FIT      = 0.15;    // clearance on the socket and the joint

// ---- derived ----
PAPER_TOP = AIM_FROM_TOP;
PAPER_BOT = AIM_FROM_TOP - PAPER_H;          // -124.75
PK_X   = PAPER_W/2 + PAPER_CLR;              // 93.5
PK_BOT = PAPER_BOT - PAPER_CLR;              // -125.75
WIN_X  = PAPER_W/2 - LIP;                    // 87.5
WIN_BOT = PAPER_BOT + LIP;                   // -119.75
TOP_Y  = BOLT_Y + BOLT_SLOT/2 + HEAD_D/2 + TOP_EDGE;   // 83.25
BOT_Y  = PK_BOT - BAR_BELOW;                 // -127.75
DT_X   = (PK_X + OUTER_X) / 2;               // centre of the full-thickness part of the leg

module arrow2d() {                           // right-hand arrow, pointing toward the centre
    tip = PK_X + ARROW_TIP;
    polygon([[tip, 0], [tip + ARROW_HEAD_L, ARROW_HEAD_W/2], [tip + ARROW_HEAD_L, -ARROW_HEAD_W/2]]);
    translate([tip + ARROW_HEAD_L - 0.5, -ARROW_TAIL_W/2]) square([ARROW_TAIL_L + 0.5, ARROW_TAIL_W]);
}

module body() difference() {
    translate([-OUTER_X, BOT_Y, 0]) cube([2*OUTER_X, TOP_Y - BOT_Y, T]);
    // window (open at the top)
    translate([-WIN_X, WIN_BOT, -1]) cube([2*WIN_X, TOP_Y - WIN_BOT + 1, T + 2]);
    // mask + witness-paper pocket from the back (open at the top)
    translate([-PK_X, PK_BOT, -1]) cube([2*PK_X, TOP_Y - PK_BOT + 1, POCKET_D + 1]);
    // witness-paper notch under the crossbar
    translate([-NOTCH_W/2, BOT_Y - 1, -1]) cube([NOTCH_W, PK_BOT - BOT_Y + 1.01, NOTCH_D + 1]);
    // faceplate screw slots
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx*BOLT_X, sy*BOLT_Y, -1])
            hull() for (d = [-BOLT_SLOT/2, BOLT_SLOT/2])
                translate([0, d, 0]) cylinder(d = BOLT_D, h = T + 2, $fn = 48);
    // centre-line arrows, recessed into the front face
    for (s = [-1, 1]) scale([s, 1, 1])
        translate([0, 0, T - ARROW_DEPTH]) linear_extrude(ARROW_DEPTH + 1) arrow2d();
}

module tongue2d() polygon([                  // right-hand tongue, pointing up into the leg
    [DT_X - DT_NECK/2, SPLIT_Y - 1],
    [DT_X - DT_NECK/2, SPLIT_Y],
    [DT_X - DT_HEAD/2, SPLIT_Y + DT_DEPTH],
    [DT_X + DT_HEAD/2, SPLIT_Y + DT_DEPTH],
    [DT_X + DT_NECK/2, SPLIT_Y],
    [DT_X + DT_NECK/2, SPLIT_Y - 1]]);

module tongues(grow = 0)
    for (s = [-1, 1]) scale([s, 1, 1])
        translate([0, 0, -1]) linear_extrude(T + 2) offset(delta = grow) tongue2d();

module bar() intersection() {                // crossbar with the lower legs and both lower screws
    body();
    union() {
        translate([-OUTER_X - 1, BOT_Y - 1, -1]) cube([2*OUTER_X + 2, SPLIT_Y - BOT_Y + 1, T + 2]);
        tongues(0);
    }
}

module right_leg() difference() {            // upper right leg with its screw
    intersection() {
        body();
        translate([0, SPLIT_Y + FIT, -1]) cube([OUTER_X + 1, TOP_Y - SPLIT_Y, T + 2]);
    }
    tongues(FIT);
}

module left_leg() mirror([1, 0, 0]) right_leg();

module laid() {
    if (PRINT_LAYOUT) translate([0, 0, T]) mirror([0, 0, 1]) children();
    else children();
}

if      (PIECE == "whole")    laid() body();
else if (PIECE == "bar")      laid() bar();
else if (PIECE == "right")    laid() right_leg();
else if (PIECE == "left")     laid() left_leg();
else if (PIECE == "assembly") { bar(); right_leg(); left_leg(); }

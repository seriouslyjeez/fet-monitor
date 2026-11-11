// freETarget rifle mask holder for a 185 x 229 mm (7.25" x 9") paper mask.
// The mask's 30.5 mm hole sits concentric with the faceplate hole; the witness paper
// runs top to bottom between the mask and the faceplate and leaves under the crossbar.
// Mounts on the faceplate's 4 screws (Rifle Face Plate.dxf / FET-016: dia 5 mm at
// x = +-100, y = +74.5 / -75.5 from the hole centre). Pocket and notch copy
// "Rifle Target Holder v17": 3 mm thick, 2 mm pocket, 98 x 1 mm witness-paper notch.
//
// Axes: origin = faceplate hole centre, X right, Y up (crossbar at the bottom),
//       Z = 0 on the back face (against the faceplate), Z = T on the front.
// Build the STLs with ../build-stls.sh.

PIECE        = "whole";   // "whole" (one piece), "bar", "left", "right", "assembly"
PRINT_LAYOUT = true;      // front face down on the bed; legs turned to fit a 200 x 220 bed

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
BAR_BELOW  = 12;    // full-thickness crossbar below the pocket
TOP_MARGIN = 5.75;  // legs run this far past the top of the mask
OUTER_X    = 108;   // half width

// ---- faceplate screws ----
BOLT_X    = 100;
BOLT_Y    = 75;     // slot centre; the 1 mm slot covers +74.5 / -75.5 either way up
BOLT_SLOT = 1;
BOLT_D    = 5;      // same as the faceplate holes (#8-32)

// ---- split version ----
DT_NECK  = 4.5;     // dovetail neck width
DT_HEAD  = 7;       // dovetail head width
DT_DEPTH = 6;       // how far the tongue reaches into the leg
FIT      = 0.15;    // clearance on the socket
LEG_TURN = 45;      // legs are laid diagonally: about 190 x 190 mm on the bed

// ---- derived ----
PAPER_TOP = AIM_FROM_TOP;
PAPER_BOT = AIM_FROM_TOP - PAPER_H;          // -124.75
PK_X   = PAPER_W/2 + PAPER_CLR;              // 93.5
PK_BOT = PAPER_BOT - PAPER_CLR;              // -125.75
WIN_X  = PAPER_W/2 - LIP;                    // 87.5
WIN_BOT = PAPER_BOT + LIP;                   // -119.75
TOP_Y  = PAPER_TOP + TOP_MARGIN;             // 110
BOT_Y  = PK_BOT - BAR_BELOW;                 // -137.75
DT_Y   = (BOT_Y + PK_BOT) / 2;

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
}

module tongue2d() polygon([
    [WIN_X - 1,        DT_Y - DT_NECK/2],
    [WIN_X,            DT_Y - DT_NECK/2],
    [WIN_X + DT_DEPTH, DT_Y - DT_HEAD/2],
    [WIN_X + DT_DEPTH, DT_Y + DT_HEAD/2],
    [WIN_X,            DT_Y + DT_NECK/2],
    [WIN_X - 1,        DT_Y + DT_NECK/2]]);

module tongues(grow = 0)
    for (s = [-1, 1]) scale([s, 1, 1])
        translate([0, 0, -1]) linear_extrude(T + 2) offset(delta = grow) tongue2d();

module bar() intersection() {
    body();
    union() {
        translate([-WIN_X, BOT_Y - 1, -1]) cube([2*WIN_X, WIN_BOT - BOT_Y + 2, T + 2]);
        tongues(0);
    }
}

module right_leg() difference() {
    intersection() {
        body();
        translate([WIN_X + FIT, BOT_Y - 1, -1]) cube([OUTER_X - WIN_X, TOP_Y - BOT_Y + 2, T + 2]);
    }
    tongues(FIT);
}

module left_leg() mirror([1, 0, 0]) right_leg();

module laid(turn = 0) {
    if (PRINT_LAYOUT) rotate([0, 0, turn]) translate([0, 0, T]) mirror([0, 0, 1]) children();
    else children();
}

if      (PIECE == "whole")    laid() body();
else if (PIECE == "bar")      laid() bar();
else if (PIECE == "right")    laid(LEG_TURN) right_leg();
else if (PIECE == "left")     laid(-LEG_TURN) left_leg();
else if (PIECE == "assembly") { bar(); right_leg(); left_leg(); }

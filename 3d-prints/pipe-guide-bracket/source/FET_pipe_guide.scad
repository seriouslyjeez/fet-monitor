// freETarget (Competition build) pipe-guide bracket
// A hex pocket traps the M10 bolt head (NUT_TRAP = false) or an M10 nut (NUT_TRAP = true),
// so the fastener is tightened from inside the target box.
// Build all four STLs with ../build-stls.sh.
// Rides the 1" steel pipe uprights so the target can't twist while raising/lowering.
// Source dims: freETarget drawings FRE-007 / FRE-008 (side panels):
//   housing outside width 304 mm, 2x dia 10 mm side holes, 250 mm apart.
//
// Model axes: X = out from the side panel (X=0 is the panel face, printed on the bed)
//             Y = front/back,  Z = vertical, Z=0 at the side-hole centre.
// Set ONE_PIECE = true for a single bracket spanning both holes (needs a >=310 mm bed).

ONE_PIECE   = false;
NUT_TRAP    = false;        // true: trap an M10 nut (bolt head inside the box, use M10x40)
                            // false: trap the hex bolt HEAD (nut inside the box, use M10x55)

IN          = 25.4;
TARGET_W    = 304;          // outside width across side panels
PIPE_GAP_W  = 14.4 * IN;    // design inside-to-inside pipe spacing (measured ~14.5")
PIPE_OD     = 33.4;         // 1" NPS steel pipe = 1.315"
HOLE_PITCH  = 250;          // side-hole spacing

U_CLEAR     = 2.3;          // radial clearance, pipe to U
ARM_T       = 6;            // U arm wall thickness
ARM_PAST    = 6;            // how far the arms extend past the U centre
LEN         = 60;           // height of each bracket head
LEAD        = 2;            // 45-degree lead-in on the U at top and bottom
BOLT_D      = 10.5;         // M10 clearance
HEX_AF      = 17.3;         // hex pocket across flats: traps a 17 mm (DIN 933) or 16 mm (ISO 4017) M10 head
HEAD_H      = NUT_TRAP ? 8 : 6.4;       // M10 nut height / M10 hex head height
HEAD_RECESS = NUT_TRAP ? 4 : 1.5;       // nut/head top sits this far below the U floor
WEB_T       = 10;           // one-piece only: web thickness

FLOOR_X   = (PIPE_GAP_W - TARGET_W) / 2;   // 30.88
U_R       = PIPE_OD/2 + U_CLEAR;           // 19.0
U_CX      = FLOOR_X + U_R;
HALF_W    = U_R + ARM_T;
X_MAX     = U_CX + ARM_PAST;
CB_BOTTOM = FLOOR_X - HEAD_RECESS - HEAD_H;

$fn = 96;

module u_cavity_2d(r) {            // in the XY plane
    translate([U_CX, 0]) circle(r);
    translate([U_CX, -r]) square([X_MAX + 10, 2*r]);
}

module u_cavity() {                // extruded along Z with lead-in flares at both ends
    translate([0,0,-LEN/2 - 1]) linear_extrude(LEN + 2) u_cavity_2d(U_R);
    // 45-degree flare: radius U_R at (LEN/2 - LEAD), U_R + LEAD + 0.5 at (LEN/2 + 0.5)
    for (s = [-1, 1])
        hull() {
            translate([0,0, s*(LEN/2 - LEAD)]) linear_extrude(0.01, center=true) u_cavity_2d(U_R);
            translate([0,0, s*(LEN/2 + 0.5)])  linear_extrude(0.01, center=true) u_cavity_2d(U_R + LEAD + 0.5);
        }
}

module bolt_cut() {
    rotate([0,90,0]) translate([0,0,-1]) cylinder(d=BOLT_D, h=X_MAX + 2);
    // hex pocket (anti-rotation) for the bolt head
    translate([CB_BOTTOM,0,0]) rotate([0,90,0]) rotate([0,0,30])
        cylinder(d=HEX_AF/cos(30), h=X_MAX, $fn=6);
}

module head_body() {
    translate([0, -HALF_W, -LEN/2]) cube([X_MAX, 2*HALF_W, LEN]);
}

module bracket() {
    difference() {
        union() {
            head_body();
            if (ONE_PIECE) {
                translate([0,0,HOLE_PITCH]) head_body();
                translate([0, -HALF_W, 0]) cube([WEB_T, 2*HALF_W, HOLE_PITCH]);
            }
        }
        u_cavity();
        bolt_cut();
        if (ONE_PIECE) translate([0,0,HOLE_PITCH]) { u_cavity(); bolt_cut(); }
    }
}

// Print orientation: panel face (X=0) down on the bed.
rotate([0,-90,0]) bracket();

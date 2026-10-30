"""fetserver/shotsolve.py: the faithful port of freETarget's hit computation (checked against a real shot from the
user's target) and the new least-squares solver (exact recovery, quantisation, misfit, three microphones).
Mutation checks (2026-10-29): all killed except two equivalents: shifting the arrival times by a constant (only
conditioning), and the port's NaN branch for ae (the NaN arises through be/acos anyway for every reachable input).
The damping loop has a hard cap: an unbounded one hung when its growth was mutated away."""
import math
import random
import unittest

from fetserver.shotsolve import E, Geometry, _solve3, forward, solve, speed_of_sound, timers_from_reported, upstream_solve

GEOM = Geometry.from_upstream({"SENSOR": 230, "ANGLE": 45})          # the user's target runs the defaults
REAL = {"counts": [427, 593, 71, 0], "x": -3.61, "y": -14.56, "T": 19.0}   # freETarget 4.3.0, 2026-10, serial 302


def points(n, rmax, seed=1):
    rng = random.Random(seed)
    for _ in range(n):
        r, th = rmax * math.sqrt(rng.random()), rng.uniform(0, 2 * math.pi)
        yield r * math.cos(th), r * math.sin(th)


class Physics(unittest.TestCase):
    def test_speed_of_sound(self):
        self.assertAlmostEqual(speed_of_sound(20, 0) * 1000, 343.3, delta=0.5)      # m/s, dry air at 20 C
        self.assertGreater(speed_of_sound(20, 80), speed_of_sound(20, 0))           # humid air is faster
        self.assertGreater(speed_of_sound(30, 50), speed_of_sound(10, 50))
        self.assertEqual(speed_of_sound(-5, 90), speed_of_sound(-5, 0), "below 0 C humidity is ignored")

    def test_frames(self):
        x, y = GEOM.to_target(*GEOM.to_sensor(3.0, -7.0))
        self.assertAlmostEqual(x, 3.0, places=12)
        self.assertAlmostEqual(y, -7.0, places=12)
        self.assertEqual(GEOM.sensors[0], (0.0, 115.0))
        g = Geometry.from_upstream({"SENSOR": 230, "NORTH_Y": 2, "SOUTH_Y": 3, "EAST_X": 1, "WEST_X": 4, "WEST_Y": -1})
        self.assertEqual(g.sensors, [(0.0, 117.0), (116.0, 0.0), (0.0, -118.0), (-119.0, -1.0)], "offsets point outward")


class Upstream(unittest.TestCase):
    def test_reproduces_a_real_shot(self):
        timers = timers_from_reported(REAL["counts"], REAL["x"], REAL["y"], GEOM)
        r = upstream_solve(timers, GEOM, REAL["T"])
        self.assertFalse(r["miss"])
        self.assertEqual((round(r["x"], 2), round(r["y"], 2)), (REAL["x"], REAL["y"]))
        self.assertEqual(r["counts"], REAL["counts"], "it prints the same counts")

    def test_its_rules(self):
        self.assertTrue(upstream_solve([100, 0, 200, 300], GEOM, 20)["miss"], "a 0 counter is a miss")
        far = upstream_solve(forward(150.0, 0.0, GEOM, 20), GEOM, 20)      # a sound from beyond the sensor ring
        self.assertFalse(far["miss"], "upstream does NOT call it a miss ...")
        self.assertTrue(math.isnan(far["x"]), "... it reports a NaN position (NaN > radius is false)")
        ring = upstream_solve(forward(110.0, 110.0, GEOM, 20), GEOM, 20)    # placed at (82, 82): 116 mm out
        self.assertEqual((ring["miss"], ring["reason"]), (True, "outside the sensor circle"))
        off = upstream_solve(forward(100.0, 60.0, GEOM, 20), GEOM, 20)["sensor_xy"]
        self.assertGreater(math.hypot(off[0] - 100, off[1] - 60), 5, "and misplaces sounds outside the opening")

    def test_nan_when_the_3d_correction_goes_complex(self):
        g = Geometry.from_upstream({"SENSOR": 230, "Z_OFFSET": 300})        # absurd z: (a + estimate) < z
        r = upstream_solve(forward(0.0, 0.0, GEOM, 20), g, 20)
        self.assertTrue(math.isnan(r["x"]), "upstream reports NaN instead of failing")

    def test_its_accuracy_on_its_own_model(self):
        worst = 0.0
        for x, y in points(300, 22):
            r = upstream_solve(forward(x, y, GEOM, 20, quantise=False), GEOM, 20)
            worst = max(worst, math.hypot(r["sensor_xy"][0] - x, r["sensor_xy"][1] - y))
        self.assertLess(worst, 0.05, "upstream's maths is sound in double precision")


class Solver(unittest.TestCase):
    def test_exact_recovery(self):
        for x, y in points(500, 60):
            s = solve(forward(x, y, GEOM, 18, quantise=False), GEOM, 18)
            self.assertTrue(s.ok, s.reason)
            self.assertAlmostEqual(s.sensor_xy[0], x, delta=1e-6)
            self.assertAlmostEqual(s.sensor_xy[1], y, delta=1e-6)
            self.assertLess(s.residual_us, 1e-6)

    def test_converges_fast(self):
        # closed-form start + exact Jacobian: under 4 iterations on average (a centre start: 4.2; no walk term in
        # the Jacobian: 7.6)
        its = [solve(forward(x, y, GEOM, 20, quantise=False), GEOM, 20).iterations for x, y in points(300, 60, seed=7)]
        self.assertLess(sum(its) / len(its), 4.0)
        self.assertLessEqual(max(its), 5)

    def test_linear_solve_pivots(self):
        self.assertEqual(_solve3([[0, 1, 0], [1, 0, 0], [0, 0, 2]], [3, 4, 10]), [4, 3, 5])
        self.assertIsNone(_solve3([[1, 2, 3], [2, 4, 6], [0, 0, 1]], [1, 2, 3]))

    def test_valid_range(self):
        # Every position out to 100 mm (the opening is 25) comes back exactly from any three or all four
        # microphones. Limitation: within ~10 mm of a microphone, with the opposite one missing, three time
        # differences have two solutions and the solver may pick the wrong one; that is inside the box, not paper.
        for x, y in points(200, 100, seed=5):
            t = forward(x, y, GEOM, 20, quantise=False)
            for missing in (None, 0, 1, 2, 3):
                tt = list(t)
                if missing is not None:
                    tt[missing] = None
                s = solve(tt, GEOM, 20)
                self.assertTrue(s.ok, (x, y, missing, s.reason))
                self.assertLess(math.hypot(s.sensor_xy[0] - x, s.sensor_xy[1] - y), 1e-4, (x, y, missing))

    def test_target_frame_and_radius(self):
        s = solve(forward(10.0, 0.0, GEOM, 20, quantise=False), GEOM, 20)
        self.assertAlmostEqual(s.x, 10 * math.cos(math.radians(45)), places=5)
        self.assertAlmostEqual(s.y, 10 * math.sin(math.radians(45)), places=5)
        self.assertAlmostEqual(s.radius, 10.0, places=5)

    def test_quantised_counters(self):
        errs = []
        for x, y in points(500, 22, seed=2):
            s = solve(forward(x, y, GEOM, 20), GEOM, 20)
            errs.append(math.hypot(s.sensor_xy[0] - x, s.sensor_xy[1] - y))
            self.assertLess(s.residual_mm, 0.06, "rounding to 8 MHz ticks is a few hundredths of a mm")
        self.assertLess(max(errs), 0.06)

    def test_misfit_shows_a_bad_microphone(self):
        t = forward(5.0, -3.0, GEOM, 20)
        good = solve(t, GEOM, 20)
        t[E] += 40                                                          # 5 us late on one microphone
        bad = solve(t, GEOM, 20)
        self.assertTrue(bad.ok)
        self.assertGreater(bad.residual_mm, 10 * good.residual_mm + 0.3)

    def test_three_microphones(self):
        for x, y in points(100, 22, seed=3):
            t = forward(x, y, GEOM, 20, quantise=False)
            for missing in range(4):
                tt = list(t)
                tt[missing] = None
                s = solve(tt, GEOM, 20)
                self.assertTrue(s.ok, s.reason)
                self.assertIsNone(s.residual_us)
                self.assertEqual(len(s.used), 3)
                self.assertAlmostEqual(s.sensor_xy[0], x, delta=1e-5)
                self.assertAlmostEqual(s.sensor_xy[1], y, delta=1e-5)

    def test_failures_say_why(self):
        self.assertEqual(solve([None, None, 5.0, 6.0], GEOM).reason, "only 2 microphones triggered")
        self.assertEqual(solve([1.0, float("nan"), 5.0, 6.0], GEOM).reason, "a counter value is not a number")
        s = solve(forward(0.0, 0.0, GEOM, 20), Geometry.from_upstream({"SENSOR": 230, "Z_OFFSET": 300}), 20)
        self.assertTrue(s.ok and all(map(math.isfinite, (s.x, s.y))), "no NaN where upstream gives one")

    def test_the_real_shot(self):
        timers = timers_from_reported(REAL["counts"], REAL["x"], REAL["y"], GEOM)
        s = solve(timers, GEOM, REAL["T"])
        self.assertLess(math.hypot(s.x - REAL["x"], s.y - REAL["y"]), 0.2, "close to what the target said")
        # but its four times do not fit one point with the default geometry: N/S late vs E/W, ~4 us (1.4 mm).
        # Measured, not assumed: the calibration work (T0) is meant to explain this.
        self.assertGreater(s.residual_us, 3.0)
        self.assertEqual([r > 0 for r in s.residuals], [True, False, True, False])

    def test_far_sounds(self):
        for x, y in ((150.0, 0.0), (100.0, 60.0), (-90.0, -90.0)):
            s = solve(forward(x, y, GEOM, 20, quantise=False), GEOM, 20)
            self.assertTrue(s.ok)
            self.assertAlmostEqual(s.sensor_xy[0], x, delta=1e-4)
            self.assertAlmostEqual(s.sensor_xy[1], y, delta=1e-4)

    def test_walk_is_part_of_the_model(self):
        t = forward(12.0, 4.0, GEOM, 20, quantise=False)
        off = Geometry.from_upstream({"SENSOR": 230, "DOPPLER": 0})
        s = solve(t, off, 20)
        self.assertGreater(math.hypot(s.sensor_xy[0] - 12, s.sensor_xy[1] - 4), 0.05, "ignoring the walk moves the shot")
        self.assertGreater(s.residual_us, 0.01)


if __name__ == "__main__":
    unittest.main()

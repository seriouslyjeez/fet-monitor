""""You may discharge air downrange" (docs/PROTOCOL.md, version 2): after the relay has ended the range officer gives the
command; a shot fired from then on (air discharged, a pellet left in the bore) is never counted and never flagged.
Before it, a shot after the end is flagged for the match director as always."""
from fetserver.matches import MatchError
from tests.test_matches import MatchCase


class Discharge(MatchCase):
    def start(self):
        fid = self.custom([("record", 60, 2)])
        mid = self.m.create("Club", fid, [{"device_id": "lane-1", "competitor": "Al Ames"}], "md")
        self.m.start(mid, "md")
        return mid

    def record_total(self, mid):
        return self.m.results(mid)["rows"][0]["total"]

    def test_after_the_command_nothing_counts_and_nothing_is_flagged(self):
        mid = self.start()
        self.at(10)
        self.shoot("lane-1", "1.00")
        self.shoot("lane-1", "2.00")
        with self.assertRaisesRegex(MatchError, "only after the relay has ended"):
            self.m.command(mid, "discharge", "ro")
        self.at(61)                                           # the relay ends
        self.assertEqual(self.m.detail(mid)["state"], "done")
        total = self.record_total(mid)
        self.at(70)
        self.shoot("lane-1", "3.00")                          # after the end, before the command: the director decides
        self.assertEqual(len(self.flags(mid)), 1)
        self.at(80)
        self.m.command(mid, "discharge", "ro")
        self.assertEqual(self.m.detail(mid)["discharged_ms"], self.clock.t)
        with self.assertRaisesRegex(MatchError, "already given"):
            self.m.command(mid, "discharge", "ro")
        self.at(81)
        self.shoot("lane-1", "0.50")                          # discharging air: expected
        self.shoot("lane-1", "0.60")
        self.assertEqual(len(self.flags(mid)), 1, "only the one before the command")
        self.assertEqual(self.record_total(mid), total, "nothing after it counts")
        with self.core.db.write() as c:                       # even in the record stream (a target switched back to
            c.execute("UPDATE shots SET stream='scored' WHERE event_ms >= ?", (self.clock.t,))   # record by hand)
        self.assertEqual(len(self.flags(mid)), 1)
        self.assertEqual(self.record_total(mid), total, "unflagged, and still not counted")

    def test_heard_not_placed_after_the_command_is_not_listed(self):
        mid = self.start()
        self.at(10)
        self.shoot("lane-1", "1.00")
        self.shoot("lane-1", "2.00")
        self.at(61)
        self.at(80)
        self.m.command(mid, "discharge", "ro")
        with self.core.db.write() as c:                       # two reports in the record stream, one after the command
            sid = c.execute("SELECT series_id FROM match_lanes WHERE match_id=?", (mid,)).fetchone()[0]
            ev = [r[0] for r in c.execute("SELECT id FROM events ORDER BY id")][-2:]
            for e, t in zip(ev, (self.clock.t - 30000, self.clock.t + 1000)):
                c.execute("INSERT INTO unplaced (event_id, device_id, event_ms, shot_number, series_id, stream) VALUES (?,?,?,?,?,?)",
                          (e, "lane-1", t, 9, sid, "scored"))
        self.assertEqual([u["event_ms"] for u in self.m.detail(mid)["unplaced"]], [self.clock.t - 30000],
                         "the one after the command is not the director's business")

    def test_a_restart_reopens_the_match_and_voids_the_command(self):
        mid = self.start()
        self.at(61)
        self.m.command(mid, "discharge", "ro")
        self.assertIsNotNone(self.m.detail(mid)["discharged_ms"])
        self.at(90)
        self.m.restart(mid, 0, "md")
        self.assertIsNone(self.m.detail(mid)["discharged_ms"], "after a restart, shots count again")

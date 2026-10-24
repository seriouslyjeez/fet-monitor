"""Drives the real dashboard in headless Chromium against a real server (skipped when Playwright is not installed)."""
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
    HAVE = True
except Exception:       # pragma: no cover
    HAVE = False

from tests.test_http import ServerCase, TOKEN
from fetserver.simtarget import SimTarget

CID = "browser-client-01"


@unittest.skipUnless(HAVE, "playwright not installed")
class ReviewInBrowser(ServerCase):
    def setUp(self):
        super().setUp()
        self.sim = SimTarget(self.url, TOKEN, "lane-1", "Lane 1", seed=5)
        self.sim.heartbeat()
        self.core.acquire_control("lane-1", CID, "Alice")
        self.core.set_mode("lane-1", CID, "sighter", shooter="Alice")
        time.sleep(0.05)
        for _ in range(3):
            self.sim.fire()
        self.sim.deliver()
        time.sleep(0.05)
        self.core.set_mode("lane-1", CID, "record", shooter="Alice")
        time.sleep(0.05)
        for _ in range(25):
            self.sim.fire()
        self.sim.deliver()
        self.core.set_mode("lane-1", CID, "ammo", shooter="Lot 5", new_series=True)
        for i in range(14):
            self.sim.fire(5.0 + (i % 5) * 0.7, 2.0 + (i % 3) * 0.9)
        self.sim.deliver()

    def test_review_clicks_keys_and_ammo(self):
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(viewport={"width": 1200, "height": 900})
            ctx.add_init_script(f"localStorage.setItem('fet_cid','{CID}');localStorage.setItem('fet_name','Alice');")
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(self.url + "/#/t/lane-1")
            pg.wait_for_selector(".title")
            pager = lambda: pg.inner_text(".pager span")
            pg.click("[data-act=gear]")
            pg.wait_for_selector("[data-act=review]")
            labels = [x.inner_text() for x in pg.query_selector_all("[data-act=review]")]
            self.assertTrue("ammo test" in labels[0] and "match" in labels[1], labels)

            # match review starts at shot 1 and steps by shot and by target
            pg.click("[data-act=review] >> nth=1")
            pg.wait_for_selector(".pager")
            self.assertEqual(pager(), "Shot 1 of 25 · target 1 of 3")
            self.assertTrue(pg.is_disabled("[data-move='step-']") and pg.is_disabled("[data-move='page-']"))
            self.assertEqual(len(pg.query_selector_all(".left svg circle[fill-opacity]")), 1)
            pg.click("[data-move='step+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 2 ')")
            pg.click("[data-move='page+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 10 ')")
            self.assertEqual(len(pg.query_selector_all(".left svg circle[fill-opacity]")), 10)
            pg.click("[data-move='page+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 20 ')")
            self.assertIn("target 2 of 3", pager())
            pg.click("[data-move='step+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 21 ')")
            self.assertEqual(len(pg.query_selector_all(".left svg circle[fill-opacity]")), 1)     # a new target picture
            pg.click("[data-move='page-']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 20 ')")
            pg.keyboard.press("ArrowLeft"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 19 ')")
            pg.keyboard.press("ArrowRight"); pg.keyboard.press("ArrowRight")
            pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 21 ')")
            pg.keyboard.press("PageDown"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 25 ')")
            self.assertTrue(pg.is_disabled("[data-move='step+']") and pg.is_disabled("[data-move='page+']"))

            # sighters toggle goes back to shot 1
            pg.click("[data-act=rstream][data-stream=sighter]")
            pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 1 of 3')")
            pg.click("[data-move='page+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 3 ')")
            pg.click("[data-act=rstream][data-stream=scored]")
            pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 1 ')")

            # ammo test review: the group grows shot by shot
            pg.click("[data-act=rback]")
            pg.click("[data-act=gear]")
            pg.wait_for_selector("[data-act=review]")
            pg.click("[data-act=review] >> nth=0")
            pg.wait_for_selector(".pager")
            self.assertEqual(pager(), "Shot 1 of 14")
            size = lambda: float(pg.inner_text(".lastbig .v"))
            self.assertEqual(size(), 4.5)
            pg.click("[data-move='step+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 2 ')")
            two = size()
            self.assertGreater(two, 4.5)
            pg.click("[data-move='page+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 10 ')")
            self.assertGreaterEqual(size(), two)
            self.assertEqual(len(pg.query_selector_all(".left svg circle[fill-opacity]")), 10)
            self.assertIn("goval", pg.inner_html(".left"))
            pg.click("[data-move='page+']"); pg.wait_for_function("document.querySelector('.pager span').innerText.startsWith('Shot 14 ')")
            self.assertTrue(pg.is_disabled("[data-move='page+']"))
            self.assertEqual(errors, [])
            b.close()


if __name__ == "__main__":
    unittest.main()

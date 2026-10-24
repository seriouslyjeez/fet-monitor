// Runs the dashboard's page logic against JSON produced by the real server (see test_dashboard.py).
const fs = require('fs'), path = require('path'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '..', 'fetserver', 'web', 'dashboard.html'), 'utf8');
const code = html.match(/<script>([\s\S]*)<\/script>/)[1];
const mod = {};
new Function('module', code)(mod);                       // no `document`, so the page does not boot
const D = JSON.parse(fs.readFileSync(0, 'utf8'));
const X = mod.exports;
const {faceSvg, esc, dec, ringR, autoRing, halfFor, zoomHalf, strip, coachView, shooterView, overlayView, shotsTable, lastPanel, ammoPanel, ammoTable, ammoMarks, KIND_MODE, reviewShot, reviewPager, reviewPage, S} = X;
const count = (s, re) => (s.match(re) || []).length;
const T = id => D.targets.find(t => t.device_id === id);
const state = (extra) => Object.assign({targets: D.targets, review: null, reviewView: null, seriesList: D.series, gear: false, adv: false, zoom: 'auto'}, extra || {});
const sh = (x, y, n) => ({n: n || 1, miss: false, x_mm: x, y_mm: y, ring: 9, decimal: 9.0, inner: false});

// --- helpers
assert.strictEqual(esc('<img src=x onerror="a">&\''), '&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;');
assert.deepStrictEqual([ringR(10), ringR(8), ringR(4), ringR(1)], [0.25, 5.25, 15.25, 22.75]);

// --- 2. zoom: Off = full target, 1-9 = that ring and stay, Auto starts at ring 8 and widens one ring at a time
assert.strictEqual(halfFor('off', [sh(20, 0)], 2.25), 26);
for (let k = 1; k <= 9; k++) assert.strictEqual(halfFor(String(k), [sh(20, 0)], 2.25), zoomHalf(k), 'fixed zoom ' + k + ' ignores shot positions');
assert.strictEqual(autoRing([], 2.25), 8, 'auto starts at the 8 ring');
assert.strictEqual(autoRing([sh(0, 1)], 2.25), 8);
const steps = [];
for (let r = 0; r <= 26; r += 0.05) { const k = autoRing([sh(r, 0)], 2.25); if (steps[steps.length - 1] !== k) steps.push(k); }
assert.deepStrictEqual(steps, [8, 7, 6, 5, 4, 3, 2, 1, 0], 'auto widens exactly one ring at a time, then shows the whole target');
assert.strictEqual(autoRing([sh(0, 0), sh(10, 0), sh(1, 1)], 2.25), 5, 'driven by the worst shot, not the latest');
assert.strictEqual(autoRing([sh(10, 0)], 2.25), 5);
assert.strictEqual(autoRing([{miss: true, x_mm: null, y_mm: null}], 2.25), 8, 'a miss has no position and does not widen the view');
// "the full shot is always visible" in auto: every pellet lies completely inside the view
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (let i = 0; i < 800; i++) {
  const shots = Array.from({length: 1 + Math.floor(rnd() * 10)}, () => { const a = rnd() * 6.283, r = rnd() * 21; return sh(r * Math.cos(a), r * Math.sin(a)); });
  const h = halfFor('auto', shots, 2.25);
  shots.forEach(s => { assert(Math.abs(s.x_mm) + 2.25 <= h && Math.abs(s.y_mm) + 2.25 <= h, 'pellet clipped in auto zoom'); });
}
let svg = faceSvg([sh(-3.61, -14.56, 1)], {half: 26});
assert(svg.includes('viewBox="-26 -26 52 52"'), 'Off shows the whole target as before');
assert(svg.includes('cx="-3.61" cy="14.56"'), 'data y is flipped for the screen');
svg = faceSvg([sh(0, 0, 1)], {half: zoomHalf(8)});
assert(svg.includes('viewBox="-6.75 -6.75 13.5 13.5"'), 'ring-8 zoom');
svg = faceSvg([sh(0, 0, 1), sh(15, 0, 2)], {half: zoomHalf(8)});
assert(svg.includes('stroke-dasharray'), 'a shot outside a fixed zoom is marked at the edge');
assert.strictEqual(count(svg, /r="2.25"/g), 1, 'only the shot inside the view is drawn as a pellet');

// --- 9. black top-right corner when the shots are not scored
const corner = s => s.includes('class="corner"');
assert(corner(faceSvg([], {half: 26, corner: true})) && !corner(faceSvg([], {half: 26})));
assert(faceSvg([], {half: 8, corner: true}).includes('points="8,-8 '), 'corner is anchored at the top right');
const sighterT = T('lane-4'), recordT = T('lane-1'), practiceT = T('lane-2');
assert.strictEqual([sighterT.mode, recordT.mode, practiceT.mode].join(), 'sighter,record,practice');
const pageOf = (t, extra) => shooterView(t, state(extra));
assert(corner(pageOf(sighterT)) && corner(pageOf(practiceT)) && !corner(pageOf(recordT)), 'corner shown for practice and sighter, not record');
assert(corner(coachView([sighterT])) && !corner(coachView([recordT])));

// --- 8. All Targets card
const card = coachView([recordT]);
const v = recordT.view;
assert(card.includes('>' + dec(v.agg.decimal) + '<') && card.includes('>' + v.agg.integer + '<') && card.includes('X ' + v.agg.x), 'total, integer and X count');
assert(card.indexOf('class="tot"') < card.indexOf('class="rows"'), 'total box is on the left');
assert(card.indexOf('class="grprow"') < card.indexOf('class="shotrow"'), 'groups on the top row, last shots on the bottom row');
assert.strictEqual(count(card, /class="cell/g), v.bull_size, 'one cell per shot position in the bull');
assert.strictEqual(count(card, /class="grp( part)?"/g), v.groups.length);
assert(card.includes('Alice'), 'shooter name shown');
assert(!/waiting on the target|controlled by|Take control|data-act/.test(card), 'nothing else under the target');
const empty = coachView([T('lane-3')]);
assert(empty.includes('offline') && empty.includes('no shots yet'));
assert(count(strip(v), /class="cell x/g) === v.bull_shots.filter(s => s.inner).length && v.bull_shots.some(s => s.inner), 'inner tens are highlighted');
assert(strip(v).includes('>M<'), 'a miss is shown as M');
// auto zoom on cards
assert(card.includes('viewBox="-' + halfFor('auto', v.bull_shots, 2.25) + ' '), 'cards auto-zoom');
assert(coachView([]).includes('No targets have reported yet'));

// --- escaping of names everywhere
const bad = T('lane-2'), hostile = /<img src=x|<script>alert|<b>Mallory|<u>Eve/i;
assert(!hostile.test(coachView(D.targets)), 'coach view escapes names');
assert(coachView([bad]).includes('&lt;img src=x') && coachView([bad]).includes('&lt;b&gt;Mallory&lt;/b&gt;'));
const asFree = t => Object.assign({}, t, {lease: {held_by: null, mine: false, expires_in_s: 0}});
const asMine = t => Object.assign({}, t, {lease: {held_by: 'Alice', mine: true, expires_in_s: 50}});
const asOther = t => Object.assign({}, t, {lease: {held_by: 'Bob <b>Mallory</b>', mine: false, expires_in_s: 50}});
[asFree, asMine, asOther].forEach(f => {
  assert(!hostile.test(shooterView(f(bad), state())), 'shooter view escapes');
  assert(!hostile.test(overlayView(f(bad), state({seriesList: D.series}), '<img src=x>')), 'overlay escapes');
});

// --- 3. right-hand order: title and info, then the shots, then last shot
const page = pageOf(asMine(recordT));
const iInfo = page.indexOf('class="title"'), iShots = page.indexOf('<h3>Shots'), iLast = page.indexOf('<h3>Last shot');
assert(iInfo > 0 && iInfo < iShots && iShots < iLast, 'order: info, shots, last shot');
assert(page.indexOf('class="zoombar"') < page.indexOf('<svg'), 'zoom bar sits above the target');
// --- 2. zoom bar: Off, 1..9, Auto in that order
const zb = page.match(/<div class="zoombar">([\s\S]*?)<\/div>/)[1];
assert.deepStrictEqual([...zb.matchAll(/data-z="([^"]+)"/g)].map(m => m[1]), ['off', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'auto']);
assert(zb.includes('class="sel">Auto'), 'the chosen zoom is highlighted');
assert(pageOf(recordT, {zoom: '8'}).includes('class="sel">8<'));
// --- 3/4. last shots table: fills from the top, numbered within the series, face and table are the current bull
const rows = shotsTable(v).match(/<tr[ >][\s\S]*?<\/tr>/g).slice(1);
assert.strictEqual(rows.length, v.bull_size, 'one row per shot position');
const filled = v.bull_shots.length;
rows.forEach((r, i) => assert.strictEqual(/<td>&nbsp;<\/td>/.test(r), i >= filled, 'row ' + i + (i < filled ? ' filled' : ' empty') + ' (top to bottom)'));
assert.deepStrictEqual([...shotsTable(v).matchAll(/<tr class="[^"]*"?>?<td>(\d+)<\/td>|<tr><td>(\d+)<\/td>/g)].map(m => +(m[1] || m[2])), v.bull_shots.map(s => s.n));
assert(v.count > v.bull_size ? v.bull_shots[0].n === v.bull_size * v.bull_index + 1 : true, 'bull starts at the right shot number');
assert(shotsTable(v).includes('class="xm">X'), 'inner ten shows an X beside the score');
assert(shotsTable(v).includes('class="miss">M'), 'miss shown');
assert.strictEqual(count(page, /<circle[^>]*fill-opacity/g), v.bull_shots.filter(s => !s.miss).length, 'the face shows exactly the shots in the table');
// --- 5/11. last shot panel: group and total, decimal and integer and X
const lp = lastPanel(v);
const g = v.groups[v.groups.length - 1];
assert(lp.includes('Group ' + (g.index + 1)) && lp.includes('<b>' + dec(g.decimal) + '</b></td><td>' + g.integer + '</td><td>' + g.x + '</td>'));
assert(lp.includes('<b>' + dec(v.agg.decimal) + '</b></td><td>' + v.agg.integer + '</td><td>' + v.agg.x + '</td>'));
assert(lp.includes('of 60') && lp.includes('Total'), 'total shows the match length');
assert(lastPanel(v).includes('class="xbig">X') === !!v.last_shot.inner);
assert(lastPanel(T('lane-3').view).includes('No shots yet'));
// --- 6. control moved under the gear; Sighter/Record on the main screen only for a match
assert(page.includes('data-act="gear"'));
assert(!/data-act="(take|release|takeover|kind|newseries|savesettings)"/.test(page), 'no control buttons on the main screen');
assert.strictEqual(count(pageOf(asMine(recordT)), /data-act="mode"/g), 2, 'sighter and record on the main screen in a match');
assert.strictEqual(count(pageOf(asMine(practiceT)), /data-act="mode"/g), 0, 'not in practice');
assert(/data-mode="record" class="sel"/.test(pageOf(asMine(recordT))) && /data-mode="sighter" class="sel"/.test(pageOf(asMine(sighterT))));
assert.strictEqual(count(pageOf(asFree(recordT)), /data-act="mode"[^>]* disabled/g), 2, 'only the person in control can use them');
const ovMine = overlayView(asMine(recordT), state(), 'Alice');
assert(/data-act="release"/.test(ovMine) && /data-kind="practice"/.test(ovMine) && /data-kind="match" class="sel"/.test(ovMine));
assert(/id="f_bull"[^>]*value="10"/.test(ovMine) && /id="f_group"/.test(ovMine) && /id="f_tp"[^>]*value="20"/.test(ovMine) && /id="f_tm"[^>]*value="60"/.test(ovMine), 'advanced settings');
assert(ovMine.includes('<details') && ovMine.includes('Advanced'));
assert(/data-act="review"/.test(ovMine), 'review is under the gear');
const ovFree = overlayView(asFree(recordT), state(), 'Al');
assert(/data-act="take"/.test(ovFree) && !/data-kind|id="f_bull"|data-act="newseries"/.test(ovFree) && /data-act="review"/.test(ovFree), 'visitors can take control and review, not change settings');
assert(/data-act="takeover"/.test(overlayView(asOther(recordT), state(), '')));
assert.strictEqual(count(ovMine, /data-act="review"/g), D.series.length);
const evil = overlayView(asMine(recordT), state({seriesList: D.series.map(g => Object.assign({}, g, {shooter: '<u>Eve</u>'}))}), 'Alice');
assert(!/<u>Eve/.test(evil) && evil.includes('&lt;u&gt;Eve&lt;/u&gt;'), 'review list escapes shooter names');
// --- review
const rv = D.review;
const rpage = shooterView(recordT, state({review: {sid: rv.series_id, stream: 'scored', shot: rv.count}, reviewView: rv}));
assert(rv.count === 15 && rv.full_count > 15 && rv.bull_index === 1, 'the review fixture is shot 15 of a longer series');
assert(rpage.includes('Reviewing') && rpage.includes('Back to live') && rpage.includes('Shot 15 of ' + rv.full_count + ' · target 2 of ' + Math.ceil(rv.full_count / rv.bull_size)));
assert.strictEqual(count(rpage, /data-act="rnav"/g), 4, 'four review buttons');
assert(/data-move="page-"[^>]*title="Back one target"/.test(rpage) && /data-move="step-"[^>]*title="Back one shot"/.test(rpage) && /data-move="step\+"[^>]*title="Forward one shot"/.test(rpage) && /data-move="page\+"[^>]*title="Forward one target"/.test(rpage));
assert(rpage.indexOf('&lt;&lt;') < rpage.indexOf('&lt;</button>') && rpage.indexOf('&gt;</button>') < rpage.indexOf('&gt;&gt;'), 'order: << < shot > >>');
assert(!/disabled/.test(rpage.slice(rpage.indexOf('class="pager"'), rpage.indexOf('class="pager"') + 900)), 'mid-series nothing is disabled');
assert.strictEqual(count(rpage, /<tr class="[^"]*"?>?<td>(1[1-5])<\/td>|<tr><td>(1[1-5])<\/td>/g), 5, 'the picture shows shots 11-15 only: the shots fired so far on this target');
assert(!rpage.includes('<td>16</td>'), 'no shot after the one being reviewed is shown');
assert(rpage.includes('Shot 15 · ring'), 'last shot panel is the reviewed shot');
assert(!/data-act="mode"/.test(rpage), 'no mode buttons while reviewing');
// --- v0.4 layout: red corner, 5-on-5 strip, every group beside Last shot
assert(faceSvg([], {half: 26, corner: true}).includes('fill="#dc2626"') && !/class="corner"[^>]*fill="#000"/.test(faceSvg([], {half: 26, corner: true})), 'corner is red');
assert(/repeat\(5,minmax\(0,1fr\)\)/.test(strip(v)), 'ten shots stack 5 over 5');
assert(/\.grprow\{[^}]*repeat\(6,/.test(require('fs').readFileSync(__dirname + '/../fetserver/web/dashboard.html', 'utf8')), 'six group slots always reserved');
v.groups.forEach(gr => assert(lp.includes('Group ' + (gr.index + 1) + '<'), 'every group stays listed: ' + gr.index));
assert(lp.indexOf('Last shot') < lp.indexOf('Group 1'));

// --- v0.5 ammo test
const ammoT = T('lane-5'), av = ammoT.view, am = av.ammo;
assert.strictEqual(av.kind, 'ammo'); assert(am.shots === 13 && av.count === 14 && am.misses === 1 && am.diameter_mm === 5.7);
const apage = shooterView(asMine(ammoT), state());
assert(apage.includes('class="goval"') && apage.includes('class="gcenter"'), 'oval and group centre are drawn on the target');
assert(!apage.includes('class="corner"'), 'no unscored corner in an ammo test');
assert(!apage.includes('<h3>Last shot') && !apage.includes('data-act="mode"') && apage.includes('class="zoombar"'), 'ammo panels replace the match panels, zoom stays');
assert(apage.includes('<h3>Group</h3>') && apage.includes(am.size_mm.toFixed(2) + '</span><span class="unit">mm'), 'group size shown big');
assert(apage.includes(am.ellipse.outer_major_mm.toFixed(2) + ' × ' + am.ellipse.outer_minor_mm.toFixed(2)), 'oval axes shown');
assert(apage.includes('x ' + am.center_x_mm.toFixed(2) + ', y ' + am.center_y_mm.toFixed(2)), 'group centre shown');
assert(apage.includes('5.70 mm projectile'), 'projectile size shown');
assert(count(apage, /<td>first shot<\/td>/g) === 1, 'the first shot is the reference');
assert.strictEqual(count(ammoTable(av), /<tr class="/g), av.bull_shots.length, 'every shot is listed, none dropped');
assert(ammoTable(av).indexOf('<td>14</td>') < ammoTable(av).indexOf('<td>13</td>') && ammoTable(av).includes('class="last"'), 'newest first');
assert(ammoTable(av).includes('class="miss">miss'), 'the miss is listed');
assert(apage.includes('r="2.85"'), 'pellets drawn at the projectile size');
assert(apage.includes('#c026d3') && apage.includes('stroke="#fff"'), 'marks have a halo so they show on black and white');
assert(!/<i>Lot 7<\/i>/.test(apage + coachView(D.targets) + overlayView(asMine(ammoT), state(), '')), 'label is escaped');
assert(apage.includes('&lt;i&gt;Lot 7&lt;/i&gt;'));
assert.strictEqual(ammoT.shooter, 'Alice'); assert.strictEqual(ammoT.label, '<i>Lot 7</i>');
assert(apage.includes('Shooter Alice · Label &lt;i&gt;Lot 7&lt;/i&gt;'), 'the info panel shows shooter and label separately');
const acard0 = coachView([ammoT]);
assert(acard0.includes('Alice · <span class="lbl">&lt;i&gt;Lot 7&lt;/i&gt;</span>'), 'the card shows both');
// the oval encloses every shot and the centre mark sits at the reported mean (checked on the numbers the page draws)
const mk = ammoMarks(av);
assert(mk.oval && mk.center && mk.oval.a >= mk.oval.b && mk.center.x === am.center_x_mm);
av.bull_shots.filter(s => !s.miss).forEach(s => {
  const t = mk.oval.ang * Math.PI / 180, dx = s.x_mm - mk.oval.cx, dy = s.y_mm - mk.oval.cy;
  const u = dx * Math.cos(t) + dy * Math.sin(t), w = -dx * Math.sin(t) + dy * Math.cos(t);
  assert((u / mk.oval.a) ** 2 + (w / mk.oval.b) ** 2 <= 1, 'shot ' + s.n + ' is inside the drawn oval');
});
assert.deepStrictEqual(ammoMarks({ammo: {shots: 1, ellipse: {cx_mm: 0, cy_mm: 0, outer_major_mm: 4.5, outer_minor_mm: 4.5, angle_deg: 0}, center_x_mm: 0, center_y_mm: 0}}), {}, 'no oval for a single shot');
assert.deepStrictEqual(ammoMarks({}), {});
// All targets card
const acard = coachView([ammoT]);
assert(acard.includes('mm group') && acard.includes('class="goval"') && acard.includes(am.size_mm.toFixed(1)) && acard.includes(' shots'));
assert(!acard.includes('class="grprow"') && !acard.includes('class="corner"'));
// gear overlay
const aov = overlayView(asMine(ammoT), state(), 'Alice');
assert(aov.includes('Ammo test') && aov.includes('data-kind="ammo"') && aov.includes('class="sel">Ammo test'), 'ammo is a mode and is selected');
assert(aov.includes('id="f_proj"') && aov.includes('value="5.7"') && aov.includes('data-mm="4.5"') && aov.includes('data-mm="5.7"'), 'projectile size can be typed or chosen');
assert(aov.includes('class="sel">.22 LR') && !aov.includes('class="sel">4.5 mm'), 'the active projectile is highlighted');
assert(!aov.includes('id="f_bull"') && aov.includes('New test') && aov.includes('Test label'));
assert(!aov.includes('id="shooter"') && !aov.includes('data-act="setshooter"'), 'the shooter cannot be edited in an ammo test');
assert(aov.includes('<h4>Shooter</h4><p><b>Alice</b></p>') && aov.includes('id="label"') && aov.includes('data-act="setlabel"') && aov.includes('value="&lt;i&gt;Lot 7&lt;/i&gt;"'), 'read-only shooter, editable label');
const nov = overlayView(asMine(recordT), state(), 'Alice');
assert(nov.includes('id="shooter"') && !nov.includes('id="label"'), 'other modes keep the shooter field');
assert(!nov.includes('f_proj') && nov.includes('id="f_bull"') && nov.includes('data-kind="ammo"'), 'other modes keep their own settings and offer Ammo test');
const revList = overlayView(asMine(recordT), state({seriesList: [{id: 9, kind: 'ammo', shooter: 'x', started: '2026-10-23T22:00:00Z', open: false, scored: {shots: 5, decimal: 0, integer: 0}, sighter: {shots: 0}, ammo: {size_mm: 7.5, shots: 5}, label: 'RWS <b>R10</b>'}]}), '');
assert(revList.includes('ammo test') && revList.includes('7.50 mm'), 'finished tests are reviewable by group size');
assert(revList.includes('RWS &lt;b&gt;R10&lt;/b&gt; · x · 5 shots'), 'the review list shows the label and the shooter');
assert(overlayView(asMine(recordT), state({seriesList: [{id: 9, kind: 'ammo', shooter: 'x', label: null, started: '2026-10-23T22:00:00Z', open: false, scored: {shots: 0, decimal: 0, integer: 0}, sighter: {shots: 0}, ammo: {shots: 0}}]}), '').includes('no label'));
const arev = shooterView(ammoT, state({review: {sid: av.series_id, stream: 'scored', shot: av.count}, reviewView: Object.assign({}, av, {open: false, started: '2026-10-23T22:00:00Z', full_count: av.count})}));
assert(arev.includes('Reviewing an ammo test') && arev.includes('<h3>Group</h3>') && !arev.includes('data-act="rstream"'));
// ammo auto zoom starts one ring tighter
assert.strictEqual(autoRing([sh(0, 0)], 2.25, 9), 9);
assert.strictEqual(autoRing([sh(0, 0)], 2.25), 8);
// a tight group: ammo auto zoom may go in to the 9 ring, where a match auto zoom stops at the 8
const tight = JSON.parse(JSON.stringify(ammoT));
tight.view.bull_shots = [sh(0, 0, 1), sh(0.3, 0, 2)]; tight.view.count = 2; tight.view.ammo.diameter_mm = 4.5;
assert(shooterView(asMine(tight), state()).includes('viewBox="-4.25 -4.25 8.5 8.5"'), 'ammo auto zoom reaches the 9 ring');
const tightMatch = JSON.parse(JSON.stringify(recordT)); tightMatch.view.bull_shots = [sh(0, 0, 1)];
assert(shooterView(asMine(tightMatch), state()).includes('viewBox="-6.75 -6.75 13.5 13.5"'), 'match auto zoom stops at the 8 ring');
assert.deepStrictEqual(KIND_MODE, {practice: 'practice', match: 'sighter', ammo: 'ammo'}, 'the gear buttons start each kind in the right mode (match always starts in sighter)');
const ov = faceSvg([sh(0, 0, 1)], {half: 8, oval: {cx: 1, cy: 2, a: 5, b: 2, ang: 30}, center: {x: 1, y: 2}});
assert(ov.includes('transform="rotate(-30 1 -2)"') && ov.includes('cy="-2"') && ov.includes('rx="5" ry="2"'), 'oval is flipped to screen coordinates and rotated the right way');
assert(ov.includes('<line x1="') && ov.includes('y1="-2"'), 'group centre cross at the reported position');
const lab = (o) => count(faceSvg([sh(0, 0, 1), sh(1, 1, 2), sh(2, 2, 3)], Object.assign({half: 8}, o)), /dominant-baseline="central" fill="#fff" stroke="#000"/g);
assert.strictEqual(lab({}), 3, 'normal faces number every shot');
assert.strictEqual(lab({labelLast: true}), 1, 'an ammo test numbers only the latest shot');
// an ammo test with only misses has no group yet and must not break any screen
const onlyMiss = JSON.parse(JSON.stringify(ammoT));
onlyMiss.view.bull_shots = [{n: 1, miss: true, ring: 0, decimal: 0, inner: false, x_mm: null, y_mm: null}]; onlyMiss.view.count = 1;
onlyMiss.view.ammo = {shots: 0, diameter_mm: 4.5, misses: 1};
assert(shooterView(asMine(onlyMiss), state()).includes('<span class="v">–</span>'));
assert(coachView([onlyMiss]).includes('no shots yet'));
assert(overlayView(asMine(recordT), state({seriesList: [{id: 9, kind: 'ammo', shooter: 'x', started: '2026-10-23T22:00:00Z', open: false, scored: {shots: 1, decimal: 0, integer: 0}, sighter: {shots: 0}, ammo: {shots: 0, diameter_mm: 4.5}}]}), '').includes('ammo test'));
assert.strictEqual(reviewPage({kind: 'ammo', bull_size: 14}), 10);
assert.strictEqual(reviewPage({kind: 'match', bull_size: 7}), 7);
// review navigation (k = shot shown, 10 shots per target picture, 25 shots in all)
const nav = (k, m, full) => reviewShot(k, 10, full === undefined ? 25 : full, m);
assert.deepStrictEqual([nav(1, 'step+'), nav(25, 'step+'), nav(1, 'step-'), nav(7, 'step-')], [2, 25, 1, 6], 'steps are clamped to the series');
assert.deepStrictEqual([nav(1, 'page+'), nav(5, 'page+'), nav(10, 'page+'), nav(11, 'page+'), nav(15, 'page+'), nav(20, 'page+'), nav(21, 'page+'), nav(25, 'page+')], [10, 10, 20, 20, 20, 25, 25, 25],
  '>> goes to the end of this target picture, or of the next if already there; the last picture ends at the last shot');
assert.deepStrictEqual([nav(1, 'page-'), nav(10, 'page-'), nav(11, 'page-'), nav(15, 'page-'), nav(20, 'page-'), nav(25, 'page-')], [1, 1, 10, 10, 10, 20],
  '<< goes to the end of the previous target picture, or shot 1');
assert.strictEqual(reviewShot(0, 10, 0, 'step+'), 0, 'an empty series stays empty');
// edges: nothing to go back to at shot 1, nothing forward at the end
const edge = (k, full) => reviewPager({count: k, full_count: full, bull_size: 10, bull_index: Math.ceil(k / 10) - 1}, false);
const dis = h => [...h.matchAll(/data-move="([^"]+)"[^>]*?( disabled)?>/g)].filter(m => m[2]).map(m => m[1]);
assert.deepStrictEqual(dis(edge(1, 25)), ['page-', 'step-'], 'at shot 1 you cannot go back');
assert.deepStrictEqual(dis(edge(25, 25)), ['step+', 'page+'], 'at the last shot you cannot go forward');
assert.deepStrictEqual(dis(edge(5, 5)), ['step+', 'page+'], 'a short last target: can go back, not forward');
assert(reviewPager({count: 0, full_count: 0, bull_size: 10, bull_index: 0}, false).includes('No shots in this series'));
// ammo review steps one shot at a time and ten at a jump, and the statistics are those of the shots so far
const ap = reviewPager({count: 7, full_count: 14, bull_size: 14, bull_index: 0}, true);
assert(ap.includes('Shot 7 of 14') && !ap.includes('target') && ap.includes('Forward ten shots') && ap.includes('Back ten shots'));
console.log('OK');

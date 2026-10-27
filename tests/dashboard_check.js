// Runs the dashboard's page logic against JSON produced by the real server (see test_dashboard.py).
const fs = require('fs'), path = require('path'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '..', 'fetserver', 'web', 'dashboard.html'), 'utf8');
const code = html.match(/<script>([\s\S]*)<\/script>/)[1];
const mod = {};
new Function('module', code)(mod);                       // no `document`, so the page does not boot
const D = JSON.parse(fs.readFileSync(0, 'utf8'));
const X = mod.exports;
const {faceSvg, esc, dec, ringR, autoRing, halfFor, zoomHalf, strip, coachView, shooterView, overlayView, shotsTable, lastPanel, ammoPanel, ammoTable, ammoMarks, KIND_MODE, reviewShot, reviewPager, reviewPage, S, infoPanel, parseCpr, clicksFor, sightAllowed, sightSince, sightResult, sightView, statusUrl, rangeView, fwJobText, updatesPanel, matchView, matchBanner, matchUsersPanel, fmtClock, remainingNow, mcConsole, commandsPanel, correctionsPanel, tvBoard, tvTargets, tvUrl, resultsPage, scoreCards, printResults, tvCompBoard, mcCompPage} = X;
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
assert.strictEqual(count(svg, /class="pellet"/g), 1, 'only the shot inside the view is drawn as a pellet');
// the outline's outer edge is the pellet's edge (a stroke is centred on the circle), for the latest shot and the others
for (const o of [{half: 26}, {half: zoomHalf(8)}, {half: 26, mini: true}, {half: 26, pellet: 5.7}]) {
  const g = faceSvg([sh(0, 0, 1), sh(3, 3, 2), Object.assign(sh(-3, 3, 3), {flyer: true}), sh(1, -2, 4)], o);
  const circles = [...g.matchAll(/<circle class="(?:pellet(?: latest)?|flyer)"[^>]* r="([\d.]+)"[^>]* stroke-width="([\d.]+)"/g)];
  assert.strictEqual(circles.length, 4, 'every shot drawn');
  circles.forEach(m => assert(Math.abs(+m[1] + m[2] / 2 - (o.pellet || 4.5) / 2) < 1e-3, 'outline outer edge is the pellet edge: ' + m[0]));
}

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
const card = X.coachCard(recordT);
assert(coachView([recordT], state()).includes('data-act="tvcfg"'), 'the TV display setup is next to the cards');
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
assert([...apage.matchAll(/<circle class="pellet(?: latest)?"[^>]* r="([\d.]+)"[^>]* stroke-width="([\d.]+)"/g)].some(m => Math.abs(+m[1] + m[2] / 2 - 2.85) < 1e-3), 'pellets drawn at the projectile size');
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
// --- sight adjustment: practice only, only for whoever is in control, only after 3 shots since the last adjustment
assert.deepStrictEqual(['4', '4.0', '3.5', '.5', '3,5', ' 2.25 ', '100'].map(parseCpr), [4, 4, 3.5, 0.5, 3.5, 2.25, 100]);
assert.deepStrictEqual(['', '0', '0.0', '-1', 'abc', '4.0.1', '1e2', '101', null, '4 clicks'].map(parseCpr), Array(10).fill(null));
// 4 clicks per ring: one click = 0.625 mm; whole clicks only, nearest, never "-0"
assert.deepStrictEqual([2.5, -2.5, 0.625, 0.31, 0.3125, -0.3125, 0.3, 0, -0.1, 1.0].map(m => clicksFor(m, 4)), [4, -4, 1, 0, 1, -1, 0, 0, 0, 2]);
assert(Object.is(clicksFor(-0.1, 4), 0), 'no negative zero');
assert.strictEqual(clicksFor(2.5, 3.5), 4, '3.5 clicks per ring: 2.5 mm = 3.5 clicks -> 4 (half rounds up)');
assert.strictEqual(clicksFor(-5.0, 2.25), -5, '2 rings at 2.25 clicks per ring = 4.5 -> 5 (away from zero)');
const btn = (t, st) => count(infoPanel(t, t.view, t.mode, st || state()), /data-act="sight"/g);
assert.strictEqual(practiceT.view.last_shot.n, 12);
assert.strictEqual(btn(asMine(practiceT)), 1, 'practice, in control, 12 shots: the button shows');
assert.strictEqual(btn(practiceT), 0, 'not without control');
assert.strictEqual(btn(asOther(practiceT)), 0, 'not when someone else is in control');
[recordT, sighterT, T('lane-5')].forEach(t => assert.strictEqual(btn(asMine(t)), 0, 'never in ' + t.mode));
const asMode = (t, mode, kind) => Object.assign(asMine(t), {mode: mode, kind: kind || t.kind});
assert.strictEqual(btn(asMode(practiceT, 'sighter', 'match')), 0);
assert.strictEqual(btn(asMode(practiceT, 'record', 'match')), 0);
assert.strictEqual(btn(asMode(practiceT, 'ammo', 'ammo')), 0);
assert.strictEqual(btn(asMode(practiceT, 'practice', 'match')), 0, 'not in a match, even in its practice mode');
assert.strictEqual(btn(asMode(practiceT, 'sighter', 'practice')), 0, 'not unless the mode is practice');
assert.strictEqual(btn(asMine(practiceT), state({review: {sid: 1}})), 0, 'not while reviewing');
const fewer = n => { const t = asMine(practiceT); t.view = Object.assign({}, t.view, {last_shot: Object.assign({}, t.view.last_shot, {n: n})}); return t; };
assert.deepStrictEqual([0, 1, 2, 3].map(n => btn(fewer(n))), [0, 0, 0, 1], 'shown from the third shot');
const sid = practiceT.view.series_id, marked = m => state({marks: {[sid]: m}});
assert.deepStrictEqual([9, 10, 12].map(m => btn(asMine(practiceT), marked(m))), [1, 0, 0], 'counts only shots since the last adjustment');
assert.strictEqual(btn(asMine(practiceT), state({marks: {[sid + 1]: 12}})), 1, 'a mark for another series does not count');
assert.strictEqual(sightSince(practiceT.view, marked(20)), 0);
// the overlay with the server's suggestion: 12 shots at x 2.50, y -1.00 -> left and up
const sg = D.sight;
assert(sg.ready && sg.kept.length === 12 && !sg.flyers.length);
assert(Math.abs(sg.move_x_mm + 2.5) < 0.01 && Math.abs(sg.move_y_mm - 1.0) < 0.01, JSON.stringify(sg));
const res = sightResult(sg, '4.0', 4.5);
assert(res.includes('Up 2 clicks') && res.includes('Left 4 clicks') && !res.includes('Down') && !res.includes('Right '), res);
assert(sightResult(sg, '2', 4.5).includes('Up 1 click<') && sightResult(sg, '2', 4.5).includes('Left 2 clicks'));
assert(sightResult(sg, 'x', 4.5).includes('Enter clicks per ring') && !sightResult(sg, 'x', 4.5).includes('Up '));
assert(sightResult(Object.assign({}, sg, {move_x_mm: 0.2, move_y_mm: -0.3}), '4', 4.5).includes('No adjustment'));
assert(sightResult(Object.assign({}, sg, {move_x_mm: 0.0, move_y_mm: -0.4}), '4', 4.5).includes('Down 1 click<'));
const fl = JSON.parse(JSON.stringify(sg)); fl.points[0].flyer = true; fl.flyers = [1];
assert.strictEqual(count(sightResult(fl, '4', 4.5), /class="flyer"/g), 1, 'a flyer is drawn hollow');
assert(sightResult(fl, '4', 4.5).includes('ignored as flyers (dashed): #1'));
const far = JSON.parse(JSON.stringify(sg)); far.points.push({n: 13, x_mm: -15, y_mm: 15, flyer: true}); far.flyers = [13];
assert.strictEqual(sightResult(far, '4', 4.5).match(/viewBox="([^"]+)"/)[1], sightResult(sg, '4', 4.5).match(/viewBox="([^"]+)"/)[1], 'zoom follows the group, not the flyer');
assert(sightResult({ready: false, min_shots: 3, on_paper: 2, misses: 1, after: 0}, '4', 4.5).includes('2 so far, 1 missed'));
const sov = sightView(asMine(practiceT), state({sight: {sid: sid, after: 0, data: sg, exit: false}, cpr: '4.0'}));
assert(sov.includes('id="cpr"') && sov.includes('inputmode="decimal"') && sov.includes('value="4.0"') && sov.includes('data-act="sightdone"'));
const sex = sightView(asMine(practiceT), state({sight: {sid: sid, after: 0, data: sg, exit: true}, cpr: '4.0'}));
assert(sex.includes('data-act="sightclear"') && sex.includes('data-act="sightkeep"') && sex.includes('Clear the target'));
assert(!sightView(asMine(practiceT), state({sight: {sid: sid, after: 0, data: sg, exit: false}, cpr: '"><b>'})).includes('"><b>'), 'the typed value is escaped');
assert(sightAllowed(asMine(practiceT), state()) && !sightAllowed(asMine(recordT), state()) && !sightAllowed(undefined, state()));
// session name (practice and match) vs shooter (ammo test)
[practiceT, recordT].forEach(t => { const o = overlayView(asMine(t), state(), 'Alice'); assert(o.includes('<h4>Session name</h4>') && !o.includes('<h4>Shooter</h4>'), t.mode); });
assert(overlayView(asMine(T('lane-5')), state(), 'Alice').includes('<h4>Shooter</h4>'), 'an ammo test keeps the shooter');
const unnamed = Object.assign(asMine(practiceT), {shooter: null});
assert(overlayView(unnamed, state(), 'Alice').includes('value="Alice – Practice"'), 'prefilled with the default');
// the target's status page link
assert.deepStrictEqual(['192.168.1.20', '10.0.0.255', 'fe80::1', '::1'].map(statusUrl), ['http://192.168.1.20/', 'http://10.0.0.255/', 'http://[fe80::1]/', 'http://[::1]/']);
assert.deepStrictEqual(['256.1.1.1', '1.2.3', 'evil.com', '1.2.3.4/x', '"><b>', null, undefined, ''].map(statusUrl), Array(8).fill(null));
[asFree, asMine, asOther].forEach(f => assert(overlayView(f(Object.assign({}, practiceT, {ip: '192.168.1.20'})), state(), '').includes('href="http://192.168.1.20/"'), 'shown to everyone'));
assert(overlayView(Object.assign({}, practiceT, {ip: null}), state(), '').includes('No address yet'));
// Range Control screen
assert(rangeView(state({rc: null})).includes('Loading'));
assert(rangeView(state({rc: {status: {password_set: false, logged_in: false}}})).includes('set-password --data-dir /var/lib/fet-monitor'));
const rcLogin = rangeView(state({rc: {status: {password_set: true, logged_in: false}, msg: '<b>x</b>'}}));
assert(rcLogin.includes('type="password"') && rcLogin.includes('data-act="rclogin"') && !rcLogin.includes('<b>x</b>') && rcLogin.includes('&lt;b&gt;x'));
const rcData = {targets: D.targets.map(asOther), banned: [{device_id: 'bad-1', name: '<i>Rogue</i>', ip: '10.0.0.9', last_seen: '2026-10-25T10:00:00Z'}]};
const rcIn = s => rangeView(state({rc: Object.assign({status: {password_set: true, logged_in: true}, data: rcData}, s || {})}));
const none = rcIn({sel: {}});
assert.strictEqual(count(none, /data-act="rcsel"/g), D.targets.length, 'one checkbox per target');
assert.strictEqual(count(none, /data-act="rc(mode|new|remove|ban)"[^>]*disabled/g), 6, 'bulk buttons disabled with nothing selected');
assert(none.includes('data-act="rcunban" data-id="bad-1"') && none.includes('&lt;i&gt;Rogue') && !none.includes('<i>Rogue'));
assert(!none.includes(D.targets.find(t => t.device_id === 'lane-2').name), 'hostile target names are escaped');
const one = rcIn({sel: {'lane-1': true}});
assert(one.includes('1 selected') && count(one, /data-act="rc(mode|new|remove|ban)"[^>]*disabled/g) === 0);
const every = {}; D.targets.forEach(t => { every[t.device_id] = true; });
assert(/data-act="rcall" checked/.test(rcIn({sel: every})), 'select-all shows checked when all are selected');
assert(rcIn({sel: {}, data: {targets: [], banned: []}}).includes('No targets.'));
// firmware updates in Range Control
const IMG = {sha256: 'ab'.repeat(32), version: '0.2.1', size: 430000, flash_size: 1048576, uploaded: 1793000000000};
const fwT = (job, fw) => ({bridge_fw: fw || '0.2.0', fw_job: job});
assert.strictEqual(fwJobText(fwT(null), [IMG]), '');
assert(fwJobText(fwT({status: 'queued', sha256: IMG.sha256}), [IMG]).includes('update to 0.2.1 waiting'));
assert(fwJobText(fwT({status: 'sent', sha256: IMG.sha256}), [IMG]).includes('updating to 0.2.1'));
assert(fwJobText(fwT({status: 'failed', sha256: IMG.sha256, result: '<b>checksum</b>'}), [IMG]).includes('failed: &lt;b&gt;checksum'));
assert(fwJobText(fwT({status: 'done', sha256: IMG.sha256}), [IMG]).includes('installed, restarting'), 'done but still the old version');
assert(fwJobText(fwT({status: 'done', sha256: IMG.sha256}, '0.2.1'), [IMG]).includes('updated to 0.2.1'));
assert(fwJobText(fwT({status: 'sent', sha256: IMG.sha256}), []).includes('abababab'), 'image deleted meanwhile: short SHA');
const fwPanel = s => rcIn(Object.assign({sel: {'lane-1': true}, images: [IMG]}, s || {}));
assert(fwPanel().includes('data-act="rcflash" data-sha="' + IMG.sha256 + '"') && fwPanel().includes('Install the chosen image (0.2.1) on 1 selected'));
assert(/data-act="rcflash"[^>]*disabled/.test(rcIn({sel: {}, images: [IMG]})), 'nothing selected: disabled');
assert(rcIn({sel: {}, images: []}).includes('No images uploaded.'));
assert(fwPanel().includes('data-act="rcpickfiles">Upload firmware files') && !fwPanel().includes('type="file"'), 'the file input is not part of the redrawn screen');
assert(/data-act="rcpickfiles" disabled>Uploading/.test(fwPanel({uploading: true})));
const res2 = fwPanel({fwResults: [{name: '<a>.bin', ok: true, text: 'stored 0.2.1'}, {name: 'b.bin', ok: false, text: 'not accepted: <x>'}]});
assert(res2.includes('<li class="mut"><b>&lt;a&gt;.bin</b>: stored 0.2.1') && res2.includes('<li class="bad"><b>b.bin</b>: not accepted: &lt;x&gt;'));
assert(fwPanel({fwMsg: '<i>no</i>', fwMsgBad: true}).includes('class="bad">&lt;i&gt;no'));
// best image hint, restart / setup portal
assert(fwJobText(Object.assign(fwT(null), {fw_best: {version: '0.2.4'}}), [IMG]).includes('0.2.4 available'));
assert(!fwJobText(Object.assign(fwT({status: 'sent', sha256: IMG.sha256}), {fw_best: {version: '0.2.4'}}), [IMG]).includes('available'), 'not while updating');
assert(fwPanel().includes('data-act="rcbest"') && fwPanel().includes('Update 1 selected to best') && fwPanel().includes('FeT Bridge firmware'));
assert(/data-act="rcbest"[^>]*disabled/.test(rcIn({sel: {'lane-1': true}, images: []})), 'no images: disabled');
assert(fwPanel().includes('data-act="rccmd" data-type="reboot"') && fwPanel().includes('data-type="wifi_portal"'));
// factory reset: exactly one target
assert(/data-act="rcreset"[^>]*disabled/.test(rcIn({sel: {}})) && /data-act="rcreset"[^>]*disabled/.test(rcIn({sel: every})));
assert(!/data-act="rcreset"[^>]*disabled/.test(rcIn({sel: {'lane-1': true}})));
// server updates panel
const U = (o) => Object.assign({current: '0.6.24', helper_installed: true, pending: false, status: null, last_check: null}, o || {});
assert.strictEqual(updatesPanel(null), '');
assert(updatesPanel(U()).includes('Running <b>v0.6.24</b>') && updatesPanel(U()).includes('data-act="rcupcheck"') && !updatesPanel(U()).includes('rcupinstall'));
const avail = U({last_check: {available: true, latest: {version: '0.6.25', notes: '<script>x</script>\nline 2', published: '2026-10-26T10:00:00Z'}}});
assert(updatesPanel(avail).includes('data-act="rcupinstall" data-v="0.6.25">Install v0.6.25'));
assert(updatesPanel(avail).includes('&lt;script&gt;x&lt;/script&gt;') && !updatesPanel(avail).includes('<script>x'), 'release notes are escaped');
assert(/rcupinstall[^>]*disabled/.test(updatesPanel(Object.assign({}, avail, {helper_installed: false}))) && updatesPanel(Object.assign({}, avail, {helper_installed: false})).includes('not installed'));
assert(/rcupinstall[^>]*disabled/.test(updatesPanel(Object.assign({}, avail, {pending: true}))));
assert(updatesPanel(U({last_check: {available: false, latest: {version: '0.6.24'}}})).includes('This is the newest release'));
assert(updatesPanel(U({status: {state: 'rolled_back', version: '0.6.25', message: 'did not start', time: 1793000000000}})).includes('class="bad">Update rolled back (v0.6.25): did not start'));
assert(fwPanel().includes('data-act="rcfwgh"'));
// Match Control
assert.deepStrictEqual([0, 999, 1000, 59001, 60000, 4500000, 3599000, 3600000].map(fmtClock), ['0:00', '0:01', '0:01', '1:00', '1:00', '1:15:00', '59:59', '1:00:00']);
assert.strictEqual(remainingNow({state: 'paused', remaining_ms: 5000, ends_at_ms: 1}, 0), 5000, 'paused: frozen');
assert(Math.abs(remainingNow({state: 'running', ends_at_ms: Date.now() + 10000}, 0) - 10000) < 100);
const MCS = (o) => state({mc: Object.assign({status: {logged_in: true, user: 'md', role: 'director', users_defined: true}, matches: [], formats: [], targets: []}, o || {})});
assert(matchView(state({mc: {status: {logged_in: false, users_defined: false}}})).includes('data-act="mclogin"') && matchView(state({mc: {status: {logged_in: false, users_defined: false}}})).includes('match-user add'));
assert(matchView(MCS()).includes('data-act="mcnew"') && matchView(MCS()).includes('Design your own match'));
const roView = matchView(MCS({status: {logged_in: true, user: 'ro', role: 'officer'}}));
assert(!roView.includes('data-act="mcnew"') && roView.includes('Range officers run matches'), 'officers do not create matches');
const FMT = {name: 'Quick', edition: 'Test', record_shots: 2, phases: [{kind: 'sight', label: '<b>Sighting</b>', seconds: 60, shots: 0, rule: '7.8', warnings: []},
  {kind: 'stage', label: 'Stage', seconds: 120, shots: 2, rule: '9.2', warnings: [{at: 30, say: 'Thirty'}]}]};
const DET = (o) => Object.assign({id: 1, name: '<i>Club</i>', state: 'running', format: FMT, record_shots: 2,
  clock: {state: 'running', paced: 'spoken', phase: 1, label: 'Stage', kind: 'stage', remaining_ms: 65000, ends_at_ms: Date.now() + 65000, next: null, extra_ms: 0, warnings: []},
  lanes: [{device_id: 'lane-1', lane: 1, target: 'Lane 1', competitor: '<u>Al</u>', online: true, sighters: 3, record: 1, in_record: false}],
  flags: [{shot_id: 9, lane: 1, n: 3, stream: 'scored', why: 'more than 2 record shots in stage', miss: false, decimal: 10.4, decision: null}]}, o || {});
const con = mcConsole({status: {role: 'director'}, detail: DET(), speak: false});
assert(con.includes('&lt;i&gt;Club') && con.includes('&lt;u&gt;Al') && con.includes('&lt;b&gt;Sighting') && !con.includes('<i>Club'), 'escaped');
assert(con.includes('data-act="mcrecord" data-id="lane-1"') && con.includes('data-mcdec="9"') && con.includes('1:05'));
assert(con.includes('data-a="pause"') && !con.includes('data-a="resume"') && con.includes('data-a="abort"'));
assert(!mcConsole({status: {role: 'officer'}, detail: DET(), speak: false}).includes('data-mcdec'), 'only directors decide');
assert(mcConsole({status: {role: 'officer'}, detail: DET({state: 'ready', flags: []}), speak: false}).includes('data-act="mcstart"'));
assert(mcConsole({status: {role: 'officer'}, detail: DET({state: 'paused', clock: Object.assign({}, DET().clock, {state: 'paused'})}), speak: false}).includes('data-a="resume"'));
const info = {name: 'Club', competitor: 'Al', lane: 2, state: 'running', label: 'Stage', kind: 'stage', next: 'Changeover', remaining_ms: 61000, shots: 20, in_record: false};
const mine = Object.assign({}, practiceT, {lease: {mine: true}}), notMine = Object.assign({}, practiceT, {lease: {mine: false}});
assert(matchBanner(info, mine, state()).includes('data-act="mcmyrecord"') && matchBanner(info, mine, state()).includes('1:01'));
assert(!matchBanner(info, notMine, state()).includes('mcmyrecord'), 'only whoever holds the lane');
assert(!matchBanner(Object.assign({}, info, {in_record: true}), mine, state()).includes('mcmyrecord'));
assert(!matchBanner(Object.assign({}, info, {kind: 'record'}), mine, state()).includes('mcmyrecord'));
assert(matchBanner(Object.assign({}, info, {state: 'ready'}), mine, state()).includes('Waiting for the start'));
assert.strictEqual(matchBanner(null, mine, state()), '');
assert(matchUsersPanel([{username: '<x>', role: 'director'}], '').includes('&lt;x&gt;') && matchUsersPanel([], '').includes('data-act="rcmuadd"'));
// during a Match Control match the shooter's own Sighter/Record buttons are not offered (the clock switches the target)
assert.strictEqual(count(infoPanel(Object.assign(asMine(recordT), {match: {state: 'running'}}), recordT.view, 'record', state()), /data-act="mode"/g), 0);
assert.strictEqual(count(infoPanel(asMine(recordT), recordT.view, 'record', state()), /data-act="mode"/g), 2);
// officer-paced commands, corrections, sighter decisions
const HOLD = DET({state: 'holding', clock: Object.assign({}, DET().clock, {state: 'holding', paced: 'officer', label: 'Record', start_say: 'Match firing. <Start>.', prev_end_say: 'End of preparation and sighting. Stop.'})});
const hp = commandsPanel(HOLD);
assert(hp.includes('Read now, then press Next') && hp.includes('End of preparation and sighting. Stop. … Match firing. &lt;Start&gt;.') && hp.includes('data-a="go"'));
const hcon = mcConsole({status: {role: 'officer'}, detail: HOLD, speak: false});
assert(!hcon.includes('data-a="pause"') && !hcon.includes('data-a="extra"') && hcon.includes('Skip this phase') && !hcon.includes('data-act="mcspeak"'), 'held: only Next / skip / stop; no speech when the officer reads');
assert(hcon.includes('ask the match director') && !hcon.includes('data-act="mcrestart"'), 'officers cannot restart');
const dcon = mcConsole({status: {role: 'director'}, detail: DET(), speak: false});
assert(count(dcon, /data-act="mcrestart"/g) === 3 && dcon.includes('id="mcrsel"'));
assert(correctionsPanel(DET({state: 'done'}), true).includes('reopens a finished match') && !correctionsPanel(DET({state: 'done'}), true).includes('Restart this phase'));
assert.strictEqual(correctionsPanel(DET({state: 'ready'}), true), '');
assert(commandsPanel(DET()).includes('id="mcsay"') && commandsPanel(DET()).includes('Given: “Stage”'));
const sflag = DET({flags: [{shot_id: 7, lane: 1, n: 1, stream: 'sighter', why: 'during set-up', miss: false, decimal: 10.1, decision: null}]});
const sc = mcConsole({status: {role: 'director'}, detail: sflag, speak: false});
assert(sc.includes('value="penalty2_first"') && sc.includes('value="miss_first"') && !sc.includes('value="annul"'), 'sighter flags get sighter decisions');
// TV displays
assert.strictEqual(tvUrl({kind: 'board', matches: [3, 4]}), '#/tv/board?m=3,4');
assert.strictEqual(tvUrl({kind: 'board', matches: []}), '#/tv/board');
assert.strictEqual(tvUrl({kind: 'targets', match: 2, targets: ['lane-1', 'a b']}), '#/tv/targets?m=2&t=lane-1,a%20b');
const B = {id: 2, name: '<b>League</b>', format_name: 'F', scoring: 'decimal', record_shots: 20, state: 'running', provisional_ranking: true,
  clock: {state: 'running', label: 'Record', remaining_ms: 61000, next: null},
  rows: [{place: 1, lane: 2, competitor: '<i>Bob</i>', target: 'Lane 2', device_id: 'lane-2', total: 104.3, x: 3, shots: 10, series: [104.3], last10: [10.1, 10.9], pending: 1},
         {place: 2, lane: 1, competitor: '', target: 'Lane 1', device_id: 'lane-1', total: 98, x: 0, shots: 10, series: [98], last10: [9.8], pending: 0}]};
const tb = tvBoard([B]);
assert(tb.includes('&lt;b&gt;League') && tb.includes('&lt;i&gt;Bob') && !tb.includes('<i>Bob'), 'escaped');
assert(tb.indexOf('&lt;i&gt;Bob') < tb.indexOf('Lane 1'), 'ordered by place');
assert(tb.includes('<th>1–10</th><th>11–20</th>') && tb.includes('>104.3<') && tb.includes('10.1 10.9') && tb.includes('10 / 20') && tb.includes('title="shots waiting'));
assert(tb.includes('data-clock="tv-2"') && tb.includes('1:01') && tb.includes('Provisional'));
assert(tvBoard([Object.assign({}, B, {scoring: 'integer', rows: [Object.assign({}, B.rows[0], {total: 98, series: [98], last10: [10, 9]})]})]).includes('>98<'), 'integer scoring without decimals');
assert(tvBoard([]).includes('No current matches'));
const tt = tvTargets(D.targets, [B], {m: '2'});
assert(tt.includes('data-clock="tv-2"') && count(tt, /class="card/g) === D.targets.filter(t => ['lane-1', 'lane-2'].includes(t.device_id)).length, 'the match\'s targets');
assert.strictEqual(count(tvTargets(D.targets, [], {t: 'lane-4'}), /class="card/g), 1, 'a chosen list of targets');
assert.strictEqual(count(tvTargets(D.targets, [], {}), /class="card/g), D.targets.length, 'all targets');
// the console says how commands are given and can switch to the other two
const spk = mcConsole({status: {role: 'director'}, detail: DET(), speak: false});
assert(spk.includes('Commands: Spoken commands') && spk.includes('data-v="officer"') && spk.includes('data-v="auto"') && !spk.includes('data-a="pacing" data-v="spoken"'));
assert(spk.includes('Turn on sound for spoken commands') && mcConsole({status: {role: 'director'}, detail: DET(), speak: true}).includes('Sound on · mute this device'));
const offc = mcConsole({status: {role: 'officer'}, detail: HOLD, speak: false});
assert(offc.includes('data-a="pacing" data-v="spoken"') && offc.includes('data-a="pacing" data-v="auto"') && offc.includes('the clock waits for Next') && !offc.includes('data-act="mcspeak"'));
const AUTO = DET({clock: Object.assign({}, DET().clock, {paced: 'auto'})});
const autoc = mcConsole({status: {role: 'director'}, detail: AUTO, speak: false});
assert(autoc.includes('Commands: Automatic (no commands)') && autoc.includes('nothing is spoken or shown to read') && autoc.includes('None: automatic') &&
       !autoc.includes('data-act="mcspeak"') && !autoc.includes('id="mcsay"') && !autoc.includes('Given:'), 'automatic means no commands at all');
// new match: the three choices in order, spoken by default
const nm = X.mcNew({draft: {lanes: {}}, formats: [], targets: []});
assert(nm.indexOf('data-v="spoken"') < nm.indexOf('data-v="officer"') && nm.indexOf('data-v="officer"') < nm.indexOf('data-v="auto"'));
assert(/data-v="spoken" class="sel"/.test(nm) && !/data-v="auto" class="sel"/.test(nm), 'spoken commands is the default');
// competitions: results, print, score cards, TV board (real server output)
const C = D.comp;
assert.deepStrictEqual(C.classes.map(c => c.name), ['Junior', 'Open']);
const rp = resultsPage(C);
assert(rp.includes('&lt;b&gt;League') && !rp.includes('<b>League') && rp.includes('&lt;i&gt;Al') && !rp.includes('<i>Al'), 'escaped');
assert(rp.indexOf('AMES') < rp.indexOf('BROWN'), 'Open: Ames (0.3 mm) ahead of Brown (1.5 mm)');
assert(rp.includes('<th>1–10</th><th>11–20</th>') && rp.includes('Ties broken by the ISSF rules'));
assert(rp.includes('href="/api/v1/results/c' + C.id + '.csv"') && rp.includes('#/print/cards/c' + C.id));
assert(rp.includes('Blue') && rp.includes('1 of 2 members'), 'an incomplete team is listed with the reason');
assert(/Red<\/td><td>AMES &lt;i&gt;Al&lt;\/i&gt;, BROWN Bo/.test(rp), 'the counting members of a ranked team');
const cards = scoreCards(C, null);
assert.strictEqual(count(cards, /class="scard"/g), 3, 'one card per competitor');
assert.strictEqual(count(scoreCards(C, String(C.classes[1].individuals[0].id)), /class="scard"/g), 1, 'a single competitor\'s card');
const amesCard = scoreCards(C, String(C.classes[1].individuals.find(e => e.family === 'Ames').id));
assert(amesCard.includes('<td>1–10</td>') && amesCard.includes('<td>11–12</td>') && count(amesCard, /class="face"/g) === 2 && amesCard.includes('Scorer / match director'));
assert(printResults(C).includes('window.print()') && printResults(C).includes('&lt;b&gt;League'));
const tb2 = tvCompBoard(C);
assert(tb2.includes('data-clock="tv-c' + C.id + '-') && tb2.includes('<h2>Junior</h2>') && tb2.includes('<h2>Open</h2>'));
assert(tvBoard([], [C]).includes('Junior'), 'the TV board shows competitions');
assert.strictEqual(tvUrl({kind: 'board', comps: [C.id], matches: []}), '#/tv/board?c=' + C.id);
const det = {id: 1, name: 'L', format: {name: 'F'}, record_shots: 12, tie_rules: 'ISSF', team_size: 2, best_count: false, paced: 'spoken',
  competitors: [{id: 5, family: 'Ames', given: 'Al', class: 'Open', team: 'Red', name: 'AMES Al', relay: null, lane: null}], relays: []};
const cpD = mcCompPage({comp: det, status: {role: 'director'}, targets: [{device_id: 'lane-9', name: 'Lane 9', online: true, in_match: false}]});
assert(cpD.includes('data-act="mcpedit" data-id="5"') && !cpD.includes('data-pe=') && cpD.includes('data-act="mcrcreate"') && cpD.includes('data-rl="lane-9"') &&
       cpD.includes('data-act="mcpimport"') && cpD.includes('Or paste a list'), 'names are edited in a window, not in the list');
const cpO = mcCompPage({comp: det, status: {role: 'officer'}, targets: []});
assert(!cpO.includes('mcpedit') && !cpO.includes('mcrcreate') && !cpO.includes('mcpimport'), 'officers see, directors change');
const kept = mcCompPage({comp: det, status: {role: 'director'}, targets: [], np: {family: 'Zed'}, npOpen: true});
assert(kept.includes('value="Zed"') && kept.includes('id="np_details" open'), 'unsaved input and the open paste section survive a redraw');
const sheet = X.competitorSheet({comp: det, editp: {id: 5, given: 'Alan'}});
assert(sheet.includes('data-pe="family"') && sheet.includes('value="Ames"') && sheet.includes('value="Alan"') && sheet.includes('data-act="mcpesave"') && sheet.includes('data-act="mcpdel"'));
const onRelay = X.competitorSheet({comp: Object.assign({}, det, {competitors: [Object.assign({}, det.competitors[0], {relay: 2, lane: 4})]}), editp: {id: 5}});
assert(onRelay.includes('On relay 2, firing point 4') && !onRelay.includes('mcpdel'), 'a competitor on a relay cannot be removed');
assert.strictEqual(X.competitorSheet({comp: det, editp: {id: 99}}), '');
// a competitor chosen for one target is not offered for another
const det2 = Object.assign({}, det, {competitors: [det.competitors[0], {id: 6, family: 'Brown', name: 'BROWN', relay: null}]});
const two = mcCompPage({comp: det2, status: {role: 'director'}, rdraft: {'lane-9': '5'}, targets: [{device_id: 'lane-9', name: 'Lane 9', in_match: false}, {device_id: 'lane-8', name: 'Lane 8', in_match: false}]});
const sel8 = two.slice(two.indexOf('data-rl="lane-8"'), two.indexOf('</select>', two.indexOf('data-rl="lane-8"')));
assert(!sel8.includes('value="5"') && sel8.includes('value="6"'));
console.log('OK');

// the latest shot has its own colour, on the big face and on the small cards; a printed score card has none
{
  const two = [{n: 1, x_mm: 1, y_mm: 1}, {n: 2, x_mm: -1, y_mm: 0}];
  for (const o of [{half: 26}, {half: 26, mini: true}]) {
    const g = faceSvg(two, o);
    assert.strictEqual(count(g, /class="pellet latest"/g), 1, 'one latest shot ' + JSON.stringify(o));
    assert(/class="pellet latest" cx="-1"[^>]*fill="var\(--last\)"/.test(g), 'the latest is the last fired, in its colour');
    assert(!/class="pellet" cx="-1"/.test(g) && /class="pellet" cx="1"[^>]*fill="var\(--m-record\)|class="pellet" cx="1"/.test(g), 'the earlier shot keeps the mode colour');
  }
  assert.strictEqual(count(faceSvg(two, {half: 26, noLast: true}), /latest/g), 0, 'score card: no latest colour');
  assert.strictEqual(count(faceSvg([{n: 1, x_mm: 0, y_mm: 0, flyer: true}], {half: 26}), /latest/g), 0, 'an ignored shot is drawn as ignored');
}

// moving a shooter (real server output): the console, the move window, the shooter's own screen
{
  const {lanesPanel, moveSheet, placeSection, mcList, competitorSheet} = X;
  const lane = (d, id) => d.lanes.find(l => l.device_id === id);
  const mv = D.moving, lm = lane(mv, 'comp-4');
  assert.strictEqual(lm.own, 'moving');
  assert.deepStrictEqual(lm.moved_from, ['comp-1']);
  const p1 = lanesPanel(mv, {}, true);
  assert(p1.includes('data-act="mclready" data-id="comp-4"') && p1.includes('value="5"'), 'Ready with the ISSF 5 minutes, editable');
  assert(p1.includes('moved from comp-1') && p1.includes('<b>Moving</b>'), 'the console says who moved and that their time stands still');
  assert.strictEqual(count(p1, /data-act="mclready"/g), 1, 'only the moving shooter can be made ready');
  const rd = D.ready, lr = lane(rd, 'comp-4');
  assert.strictEqual(lr.own, 'extra'); assert(lr.sighting);
  const p2 = lanesPanel(rd, {}, false);
  assert(p2.includes('data-act="mclrecord" data-id="comp-4"') && /data-lends="\d+"/.test(p2), 'sighting, then Start record; their own clock ticks');
  assert(p2.includes('data-act="mcmove"'), 'a range officer can move a shooter during the match');
  assert(!lanesPanel(Object.assign({}, rd, {state: 'ready'}), {}, false).includes('mcmove'), 'before the start only the director moves');
  // the move window lists the free targets only, with the rulebook text; names are escaped
  const sheet = moveSheet({detail: rd, movep: {from: 'comp-2'}, targets: D.mtargets});
  assert(!sheet.includes('value="comp-3"') && !sheet.includes('value="comp-4"'), 'targets in a match are not offered');
  assert(sheet.includes('ISSF 6.10.9.2') && sheet.includes('stop their time'));
  assert(p1.includes('Spare &lt;4&gt;') && !p1.includes('Spare <4>'), 'target names are escaped');
  assert(moveSheet({detail: rd, movep: {from: 'nope'}, targets: D.mtargets}) === '', 'an unknown lane: no window');
  // the shooter's own screen: their own time and Start record after sighters
  const info = D.movedTarget.match;
  assert(info.sighting && info.label.includes('your own time'));
  const ban = matchBanner(info, Object.assign({}, D.movedTarget, {lease: {mine: true}}), {});
  assert(ban.includes('data-act="mcmyrecord"') && ban.includes('Sighting after the move'), 'the shooter can start record after sighters');
  assert(ban.includes('(moved here from comp-1)'), 'the banner says where they came from');
  // the competitor window: before the start a place can be changed; once the relay runs, the console does it
  const cd = D.cdetail, ames = cd.competitors.find(p => p.family === 'Ames');
  const before = placeSection({comp: cd, targets: D.mtargets, editp: {id: ames.id}}, ames);
  assert(before.includes('Open the console of relay 1'), 'the relay has started: moving is on its console');
  const ready = JSON.parse(JSON.stringify(cd)); ready.relays[0].state = 'ready';
  const pl = placeSection({comp: ready, targets: D.mtargets, editp: {id: ames.id}}, ames);
  assert(pl.includes('data-pm="mrel"') && pl.includes('here now') && pl.includes('(swap)'), 'relay and target choice, swap shown');
  // Match Control home: relays are listed with their competition, not as single matches
  const home = mcList({status: {user: 'md', role: 'director'}, comps: [{id: 1, name: 'L', format_name: 'F', competitors: 3, active: true, relays: [{id: 7, relay: 1, state: 'running'}]}],
    matches: [{id: 7, name: 'L - relay 1', format_name: 'F', state: 'running', competition_id: 1, relay: 1, clock: {}}, {id: 8, name: 'Solo', format_name: 'F', state: 'ready', competition_id: null, clock: {}}], formats: []});
  assert(home.includes('data-act="mcopen" data-id="7"') && home.includes('Relay 1: running') && home.includes('Competitors &amp; relays'));
  assert.strictEqual(count(home, /L - relay 1/g), 0, 'the relay is not repeated as a single match');
  assert(home.includes('Solo'));
  assert(mcList({status: {user: 'md', role: 'director'}, compsErr: 'boom', matches: []}).includes('Could not load the competitions: boom'));
  console.log('moves OK');
}

// results of single matches and saved copies; deleting from Match Control (real server output)
{
  const {resultsIndex, mcList} = X;
  const sp = resultsPage(D.single);
  assert(sp.includes('&lt;i&gt;Night') && !sp.includes('<i>Night') && sp.includes('/results/' + D.single.key + '.csv') && !sp.includes('Teams CSV'), 'a single match: no teams');
  assert.strictEqual(count(scoreCards(D.single, null), /class="scard"/g), 1);
  const sv = resultsPage(D.saved);
  assert(D.saved.key[0] === 'a' && sv.includes('saved copy from') && sv.includes('#/print/results/' + D.saved.key));
  assert(resultsPage({missing: true}).includes('were deleted') && scoreCards({missing: true}).includes('deleted') && printResults({missing: true}).includes('deleted'));
  const idx = resultsIndex(D.listing);
  assert(idx.includes('Saved copy') && idx.includes('Competition') && idx.includes('#/results/' + D.saved.key) && !idx.includes('<i>Night'), 'the index lists every kind');
  assert(resultsIndex([]).includes('No results yet'));
  const base = {status: {user: 'md', role: 'director'}, formats: [],
    comps: [{id: 1, name: 'L', format_name: 'F', competitors: 2, active: true, busy: true, results_hidden: false, relays: []},
            {id: 2, name: 'Old', format_name: 'F', competitors: 2, active: false, busy: false, results_hidden: true, relays: []}],
    matches: [{id: 8, name: 'Solo', format_name: 'F', state: 'done', competition_id: null, results_hidden: false, clock: {}}],
    results: [{key: 'c2', kind: 'competition', name: 'Old', at_ms: 0, hidden: true}, {key: 'a3', kind: 'frozen', name: 'Gone', at_ms: 0, hidden: false}]};
  const h = mcList(Object.assign({}, base, {msel: {c2: true, m8: true, c1: true}, rsel: {a3: true}}));
  assert(/data-msel="c1" disabled/.test(h), 'a running competition cannot be selected');
  assert(/data-msel="c2" checked/.test(h) && /data-msel="m8" checked/.test(h) && !/data-msel="c1" checked/.test(h));
  assert(h.includes('Delete selected (2)'), 'a running competition ticked earlier is not counted: ' + h.match(/Delete selected[^<]*/)[0]);
  assert.deepStrictEqual(X.deletable(Object.assign({}, base, {msel: {c2: true, m8: true, c1: true, m99: true}})).map(x => x[0]), ['c2', 'm8'], 'only what can be deleted is sent');
  assert(h.includes('(results deleted)') && h.includes('data-act="mcresshow" data-key="c2"') && !/data-rsel="c2"/.test(h), 'deleted results: show again, not selectable');
  assert(h.includes('Delete selected results (1)') && h.includes('#/results/m8'));
  const ro = mcList(Object.assign({}, base, {status: {user: 'ro', role: 'officer'}}));
  assert(!ro.includes('data-msel') && !ro.includes('mcresdel') && !ro.includes('<h3>Results</h3>'), 'officers do not delete');
  console.log('delete OK');
}

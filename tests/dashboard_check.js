// Runs the dashboard's page logic against JSON produced by the real server (see test_dashboard.py).
const fs = require('fs'), path = require('path'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '..', 'fetserver', 'web', 'dashboard.html'), 'utf8');
const code = html.match(/<script>([\s\S]*)<\/script>/)[1];
const mod = {};
new Function('module', code)(mod);                       // no `document`, so the page does not boot
const D = JSON.parse(fs.readFileSync(0, 'utf8'));
const X = mod.exports;
const {faceSvg, esc, dec, ringR, autoRing, halfFor, zoomHalf, strip, coachView, shooterView, overlayView, shotsTable, lastPanel, S} = X;
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
const rpage = shooterView(recordT, state({review: {sid: rv.series_id, stream: 'scored', bull: rv.bull_index}, reviewView: rv}));
assert(rpage.includes('Reviewing') && rpage.includes('Back to live') && rpage.includes('Target ' + (rv.bull_index + 1) + ' of ' + rv.bull_count));
assert(rv.bull_count > 1 && rpage.includes('data-act="bull"'));
assert(!/data-act="mode"/.test(rpage), 'no mode buttons while reviewing');
console.log('OK');
// --- v0.4 layout: red corner, 5-on-5 strip, every group beside Last shot
assert(faceSvg([], {half: 26, corner: true}).includes('fill="#dc2626"') && !/class="corner"[^>]*fill="#000"/.test(faceSvg([], {half: 26, corner: true})), 'corner is red');
assert(/repeat\(5,minmax\(0,1fr\)\)/.test(strip(v)), 'ten shots stack 5 over 5');
assert(/\.grprow\{[^}]*repeat\(6,/.test(require('fs').readFileSync(__dirname + '/../fetserver/web/dashboard.html', 'utf8')), 'six group slots always reserved');
v.groups.forEach(gr => assert(lp.includes('Group ' + (gr.index + 1) + '<'), 'every group stays listed: ' + gr.index));
assert(lp.indexOf('Last shot') < lp.indexOf('Group 1'));

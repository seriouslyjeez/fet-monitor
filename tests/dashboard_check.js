// Runs the dashboard's page logic against JSON produced by the real server (see test_dashboard.py).
const fs = require('fs'), path = require('path'), assert = require('assert');
const html = fs.readFileSync(path.join(__dirname, '..', 'fetserver', 'web', 'dashboard.html'), 'utf8');
const code = html.match(/<script>([\s\S]*)<\/script>/)[1];
const mod = {};
new Function('module', code)(mod);                       // no `document`, so the page does not boot
const D = JSON.parse(fs.readFileSync(0, 'utf8'));
const {faceSvg, esc, dec, ringR, total, S, coachView, shooterView} = mod.exports;

// --- pure helpers
assert.strictEqual(esc('<img src=x onerror="a">&\''), '&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;');
assert.deepStrictEqual([ringR(10), ringR(4), ringR(1)], [0.25, 15.25, 22.75]);
assert.strictEqual(dec(null), '–');
assert.deepStrictEqual(total([{decimal: 10.9, ring: 10}, {decimal: 10.2, ring: 10}, {decimal: 0, ring: 0, miss: true}]), {dec: 21.1, int: 20, misses: 1});

// --- target face: geometry and the y flip (data y is up, screen y is down)
const live = {shot_number: 1, miss: false, x_mm: -3.61, y_mm: -14.56, ring: 4, decimal: 4.9};
const svg = faceSvg([live, {shot_number: 2, miss: true, x_mm: null, y_mm: null, ring: 0, decimal: 0}], {});
assert(svg.includes('cx="-3.61" cy="14.56"'), 'shot drawn with y flipped');
assert.strictEqual((svg.match(/r="2.25"/g) || []).length, 1, 'one pellet drawn, the miss is not');
assert(svg.includes('#e11d48'), 'last shot is outlined');
assert.strictEqual((svg.match(/<circle/g) || []).length, 1 + 10 + 1, 'black disc + 10 rings + 1 pellet');
assert(!faceSvg([live], {mini: true}).includes('<text'), 'mini face has no labels');
assert(faceSvg([], {pellet: 5.5}) && faceSvg([live], {pellet: 5.5}).includes('r="2.75"'), 'pellet size follows calibre');

// --- coach grid from the real /targets output
S.targets = D.targets;
const coach = coachView();
assert(!/<img src=x|<script>alert|<b>Mallory/i.test(coach), 'hostile target/shooter names must be escaped');
assert(coach.includes('&lt;img src=x'), 'hostile name is shown as text');
assert(coach.includes('&lt;b&gt;Mallory&lt;/b&gt;'), 'hostile shooter name is shown as text');
assert(coach.includes('of 3 targets online') || coach.includes('of ' + D.targets.length + ' targets online'));
assert(coach.includes('offline, last seen'), 'offline target is flagged');
assert(coach.includes('shots waiting on the target'), 'queued shots are shown');
assert(coach.includes('controlled by '), 'who is in control is shown');
assert.strictEqual((coach.match(/class="card/g) || []).length, D.targets.length);
S.targets = [];
assert(coachView().includes('No targets have reported yet'));

// --- shooter view in each control state
function view(t, extra) {
  S.targets = [t]; S.segments = D.segments; S.shots = D.shots; S.viewSeg = null; Object.assign(S, extra || {});
  return shooterView(t);
}
const base = D.targets.find(t => t.device_id === 'lane-1');
const mine = view(Object.assign({}, base, {lease: {held_by: 'Alice', mine: true, expires_in_s: 50}}));
assert.strictEqual((mine.match(/data-act="mode"/g) || []).length, 3, 'three mode buttons when in control');
assert(mine.includes('data-act="release"') && mine.includes('data-act="newseries"'));
assert(!/<img src=x|<script>alert|<b>Mallory|<b>Bob/i.test(mine));
const other = view(Object.assign({}, base, {lease: {held_by: 'Bob <b>', mine: false, expires_in_s: 50}}));
assert(other.includes('data-act="takeover"') && !other.includes('data-act="mode"'));
assert(other.includes('Bob &lt;b&gt;'));
const free = view(Object.assign({}, base, {lease: {held_by: null, mine: false, expires_in_s: 0}}));
assert(free.includes('data-act="take"') && !free.includes('data-act="mode"'));
assert(free.includes('Last shot'));
const past = view(Object.assign({}, base, {lease: {held_by: null, mine: false, expires_in_s: 0}}), {viewSeg: D.segments.find(g => !g.open).id});
assert(past.includes('reviewing a past series') && past.includes('Series'));
assert(past.includes('selected'), 'the chosen past series is selected in the list');
// hostile shooter name inside the shooter view and in the review list
const bad = D.targets.find(t => t.device_id === 'lane-2');
const hv = view(Object.assign({}, bad, {lease: {held_by: null, mine: false, expires_in_s: 0}}));
assert(!/<b>Mallory|<img src=x/i.test(hv), 'shooter view escapes shooter and target names');
assert(hv.includes('&lt;b&gt;Mallory&lt;/b&gt;'));
const rv = view(Object.assign({}, base, {lease: {held_by: null, mine: false, expires_in_s: 0}}));
assert(!/<u>Eve/i.test(rv) && rv.includes('&lt;u&gt;Eve&lt;/u&gt;'), 'review list escapes shooter names');
console.log('OK');

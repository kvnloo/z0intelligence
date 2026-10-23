// Interactive next-state values come from JavaScript emitted by bend 2.0.34.
// The authored adapter validates the ABI and derives the verdict/reason labels; the recorded tape is played back.
import { transition } from '../bend/fsm/adapter.mjs';
import { bendMetadata as META } from '../bend/fsm/Runtime.generated.mjs';
import MEM from '../bend/memory/MemRuntime.mjs';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v; else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
};
const SVG = 'http://www.w3.org/2000/svg';
const s = (tag, attrs = {}, ...kids) => {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
};
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const lawById = Object.fromEntries(META.laws.map((l) => [l.id, l]));

/* ───────────── rail + reveal ───────────── */
function rail() {
  const links = $$('.rail a'); const pill = $('.rail .pill');
  const place = (a) => {
    links.forEach((l) => l.setAttribute('aria-current', l === a ? 'true' : 'false'));
    if (matchMedia('(max-width: 900px)').matches) { pill.style.left = a.offsetLeft + 'px'; pill.style.top = ''; const r = a.parentElement; r.scrollTo({ left: a.offsetLeft - r.clientWidth / 2 + a.offsetWidth / 2, behavior: 'smooth' }); }
    else { pill.style.top = a.offsetTop + 'px'; pill.style.left = ''; }
  };
  const byId = Object.fromEntries(links.map((a) => [a.dataset.ch, a]));
  place(links[0]);
  const io = new IntersectionObserver((es) => {
    for (const e of es) if (e.isIntersecting && byId[e.target.id]) place(byId[e.target.id]);
  }, { rootMargin: '-45% 0px -50% 0px' });
  $$('[data-chapter]').forEach((c) => io.observe(c));
  addEventListener('resize', () => place(links.find((l) => l.getAttribute('aria-current') === 'true') || links[0]));
  const rv = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); rv.unobserve(e.target); } }), { rootMargin: '0px 0px -8% 0px' });
  $$('.reveal').forEach((el) => rv.observe(el));
}

/* ───────────── 01 incidents ───────────── */
const SHORT = { W1: 'one owner per worktree', L3: 'receipt bound to the head', L5: 'interrupted is never dropped', L1: 'no merge without owner acceptance', L4: 'deployed == integrated', W4: 'heavy work off the desktop' };
const INCIDENTS = [
  { id: 'W1', what: 'writes by a second agent session into a worktree another session already held (19 worktrees)', md: 'One agent per worktree.' },
  { id: 'L3', what: 'integrations with no recorded passing receipt for the head being landed. The law also blocks reusing an older commit’s green receipt', md: 'Make sure tests pass before merging.' },
  { id: 'L5', what: 'interrupted work items with no recorded resume, PR or explicit abandonment', md: 'Resume dead workers from their log.' },
  { id: 'L1', what: 'PRs merged with no recorded owner acceptance of a deployed head', md: 'Never merge before Kevin has seen it.' },
  { id: 'L4', what: 'services found stale, diverged or missing merged commits; the running build was not the integrated one', md: 'Restart the service after merging.' },
  { id: 'W4', what: 'heavy builds and tests run on the interactive desktop with no exception, instead of on the build machine', md: 'Heavy work goes to the build machine.' },
];
function incidents() {
  const root = $('#incidents');
  for (const inc of INCIDENTS) {
    const law = lawById[inc.id] || (META.laws.find((l) => l.id === inc.id));
    const n = law?.incidents?.count ?? 0;
    const num = h('div', { class: 'num', 'data-n': n }, n.toLocaleString());
    root.append(h('div', { class: 'incident reveal' }, num, h('div', { class: 'what' }, inc.what),
      h('div', { class: 'md' }, h('s', {}, inc.md), ' → ', h('span', { class: 'lawref' }, `law ${inc.id}: ${SHORT[inc.id]}`))));
  }
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (!e.isIntersecting) return; io.unobserve(e.target);
    $$('.num', e.target.parentElement).forEach((el) => {
      const to = +el.dataset.n; if (reduced) { el.textContent = to.toLocaleString(); return; }
      const t0 = performance.now(); const tick = (t) => { const k = Math.min(1, (t - t0) / 1100); el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3))).toLocaleString(); if (k < 1) requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    });
  }), { threshold: .3 });
  io.observe(root.firstElementChild);
}

/* ───────────── statechart (edges derived by executing the compiled model) ───────────── */
const W = 150, H = 58;
const NODES = [ // index = WorkItem.Item constructor index
  { tag: 'Proposed', x: 6, y: 168, ev: 'nobody owns it' },
  { tag: 'Claimed', x: 218, y: 168, ev: 'au' },
  { tag: 'Implementing', x: 430, y: 168, ev: 'au · hd   ↺ push' },
  { tag: 'Verifying', x: 642, y: 168, ev: 'au · hd' },
  { tag: 'DraftPR', x: 854, y: 168, ev: 'au · hd' },
  { tag: 'Integrated', x: 854, y: 330, ev: '+ ig' },
  { tag: 'Deployed', x: 642, y: 330, ev: '+ dp (served)' },
  { tag: 'Accepted', x: 430, y: 330, ev: '+ ap (owner)' },
  { tag: 'Merged', x: 218, y: 330, ev: 'final' },
  { tag: 'Blocked', x: 324, y: 14, ev: 'au · hd', side: true },
  { tag: 'Interrupted', x: 748, y: 14, ev: 'pushed hd kept', side: true },
  { tag: 'Abandoned', x: 6, y: 14, ev: 'final', side: true },
];
const IDX = Object.fromEntries(NODES.map((n, i) => [n.tag, i]));
// [from, to, event, laws, bend]; edges were derived by executing the compiled model over sample states x events
const EDGES = [
  ['Proposed', 'Claimed', 'Claim', ['L6']], ['Claimed', 'Implementing', 'Start', ['L2']], ['Implementing', 'Verifying', 'Submit', ['L2']],
  ['Verifying', 'DraftPR', 'Verify', ['L2', 'L3']], ['DraftPR', 'Integrated', 'Integrate', ['L2', 'L3']], ['Integrated', 'Deployed', 'Deploy', ['L4']],
  ['Deployed', 'Integrated', 'Observe drift', ['L4'], -46], ['Deployed', 'Accepted', 'Accept', ['L1', 'L2', 'L7']], ['Accepted', 'Merged', 'Merge', ['L1']],
  ['Verifying', 'Implementing', 'Verify fails', [], -40],
  ['Implementing', 'Blocked', 'Block', [], 16], ['Blocked', 'Implementing', 'Unblock', [], 16], ['Verifying', 'Blocked', 'Block', []],
  ['Implementing', 'Interrupted', 'Interrupt', []], ['Interrupted', 'Implementing', 'Resume', ['L5', 'L6'], -22], ['Verifying', 'Interrupted', 'Interrupt', []],
  ['Blocked', 'Abandoned', 'Abandon', ['L5']], ['Proposed', 'Abandoned', 'Abandon', ['L5']],
];
const center = (n) => ({ x: n.x + W / 2, y: n.y + H / 2 });
function clip(from, to, n) { // where the segment from->to leaves rect n (centered at from)
  const dx = to.x - from.x, dy = to.y - from.y; const sx = (W / 2 + 4) / Math.abs(dx || 1e-6), sy = (H / 2 + 4) / Math.abs(dy || 1e-6); const k = Math.min(sx, sy);
  return { x: from.x + dx * k, y: from.y + dy * k };
}
function makeChart(host, { laws = true } = {}) {
  const svg = s('svg', { viewBox: '0 0 1010 400', role: 'img', 'aria-label': 'Work-item statechart' });
  svg.append(s('defs', {}, s('marker', { id: host.id + '-ar', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
    s('path', { d: 'M0,0 L10,5 L0,10 z', fill: 'context-stroke' }))));
  const edgeEls = EDGES.map(([a, b, label, lw, bend = 0]) => {
    const A = center(NODES[IDX[a]]), B = center(NODES[IDX[b]]);
    const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2; const nx = -(B.y - A.y), ny = B.x - A.x; const len = Math.hypot(nx, ny) || 1;
    const c = { x: mx + (nx / len) * bend, y: my + (ny / len) * bend };
    const p0 = clip(A, bend ? c : B), p1 = clip(B, bend ? c : A);
    const d = bend ? `M${p0.x},${p0.y} Q${c.x},${c.y} ${p1.x},${p1.y}` : `M${p0.x},${p0.y} L${p1.x},${p1.y}`;
    const lx = bend ? (p0.x + 2 * c.x + p1.x) / 4 : (p0.x + p1.x) / 2, ly = bend ? (p0.y + 2 * c.y + p1.y) / 4 : (p0.y + p1.y) / 2;
    const g = s('g', { class: 'edge', 'data-from': a, 'data-to': b, 'data-laws': lw.join(' ') },
      s('title', {}, `${label}${lw.length ? ' · guarded by ' + lw.join(', ') : ''}`),
      s('path', { d, 'marker-end': `url(#${host.id}-ar)`, stroke: 'currentColor' }),
      lw.length ? s('text', { x: lx, y: ly - 7, 'text-anchor': 'middle' }, lw.join('·')) : null);
    svg.append(g); return g;
  });
  const nodeEls = NODES.map((n) => {
    const g = s('g', { class: 'node' + (n.side ? ' side' : ''), 'data-tag': n.tag },
      s('rect', { x: n.x, y: n.y, width: W, height: H, rx: 13 }),
      s('text', { x: n.x + 16, y: n.y + 25 }, n.tag),
      s('text', { x: n.x + 16, y: n.y + 44, class: 'sub' }, n.ev));
    svg.append(g); return g;
  });
  const token = s('circle', { class: 'token', r: 7, cx: NODES[0].x + W - 14, cy: NODES[0].y + 14 });
  svg.append(token);
  host.append(svg);
  host.append(h('div', { class: 'legend' }, h('span', { class: 'meta' }, h('span', { class: 'dot', style: 'background:var(--gold)' }), ' current'),
    h('span', { class: 'meta' }, h('span', { class: 'dot no' }), ' refused here'), h('span', { class: 'meta' }, h('span', { class: 'dot hold' }), ' held')));
  const api = {
    visited: new Set(),
    set(tag, verdict, from, event) {
      const n = NODES[IDX[tag]]; this.visited.add(tag);
      nodeEls.forEach((el) => { const t = el.dataset.tag; el.classList.toggle('on', t === tag); el.classList.toggle('was', t !== tag && this.visited.has(t)); el.classList.remove('no', 'hold'); });
      if (verdict === 'refused') nodeEls[IDX[tag]].classList.add('no');
      if (verdict === 'held') nodeEls[IDX[tag]].classList.add('hold');
      edgeEls.forEach((e) => e.classList.toggle('hot', verdict === 'accepted' && e.dataset.from === from && e.dataset.to === tag));
      token.setAttribute('cx', n.x + W - 14); token.setAttribute('cy', n.y + 14);
      // Phones: the chart scrolls sideways, so keep the current state in view.
      if (host.scrollWidth > host.clientWidth + 4) {
        const k = svg.getBoundingClientRect().width / 1010;
        const svgLeft = svg.getBoundingClientRect().left - host.getBoundingClientRect().left + host.scrollLeft;
        const left = svgLeft + (n.x + W / 2) * k - host.clientWidth / 2;
        host.scrollTo({ left: Math.max(0, left), behavior: reduced ? 'auto' : 'smooth' });
      }
    },
    reset() { this.visited.clear(); this.set('Proposed'); },
    laws(ids) { edgeEls.forEach((e) => e.classList.toggle('lawhi', ids.length > 0 && e.dataset.laws.split(' ').some((l) => ids.includes(l)))); },
  };
  api.set('Proposed');
  return api;
}

/* ───────────── 02 laws list ───────────── */
function lawsChapter() {
  const chart = makeChart($('#chart-laws'));
  const list = $('#law-list'); let cur = null;
  for (const l of META.laws.filter((l) => l.loop === 'work-item')) {
    const b = h('button', { class: 'law', type: 'button', 'aria-pressed': 'false' },
      h('span', { class: 'id' }, l.id), h('span', { class: 'nm' }, l.name), h('span', { class: 'tx' }, 'Prevents: ' + l.prevents));
    b.addEventListener('click', () => {
      cur = cur === l.id ? null : l.id;
      $$('.law', list).forEach((x) => x.setAttribute('aria-pressed', x === b && cur ? 'true' : 'false'));
      chart.laws(cur ? [cur] : []);
    });
    list.append(b);
  }
}

/* ───────────── 03 the game ───────────── */
const ACT = { opus: { id: 1, role: 'Opus' }, ci: { id: 2, role: 'Ci' }, sol: { id: 3, role: 'Sol' }, owner: { id: 4, role: 'Owner' }, free: { id: 5, role: 'Free' }, chat: { id: 6, role: 'Chat' }, jev: { id: 7, role: 'Jev' } };
const WHO = { Opus: 'Opus agent', Ci: 'CI', Sol: 'Sol agent', Owner: 'Kevin', Free: 'free model', Chat: 'chat app', Jev: 'Jev (fast chooser)' };
function movesFor(st) {
  const au = st.au || ACT.opus; const hd = st.hd ?? 0;
  const other = au.role === 'Opus' ? ACT.sol : ACT.opus;
  const m = (who, act, event, tempt = false, dot = '') => ({ who, act, event, tempt, dot });
  switch (st.tag) {
    case 'Proposed': return [m(ACT.free, 'claim the work (free tier, fast)', { tag: 'Claim', a: ACT.free }, true), m(ACT.chat, 'claim it from the ChatGPT app', { tag: 'Claim', a: ACT.chat }, true), m(ACT.opus, 'claim the work', { tag: 'Claim', a: ACT.opus }), m(ACT.sol, 'claim the work', { tag: 'Claim', a: ACT.sol })];
    case 'Claimed': return [m(other, 'start on it anyway', { tag: 'Start', a: other }, true), m(au, 'start implementing', { tag: 'Start', a: au }), m(null, 'worker process dies', { tag: 'Interrupt' })];
    case 'Implementing': return [m(other, 'push a commit to their branch', { tag: 'Push', a: other, sha: hd + 1 }, true), m(au, `push a commit (head ${hd + 1})`, { tag: 'Push', a: au, sha: hd + 1 }), m(au, 'submit for verification', { tag: 'Submit', a: au }), m(null, 'worker process dies', { tag: 'Interrupt' }), m(au, 'mark blocked', { tag: 'Block', a: au })];
    case 'Verifying': return [m(au, 'verify my own work: “tests pass”', { tag: 'Verify', a: au, r: { sha: hd, pass: true } }, true), m(ACT.ci, `use a green receipt for head ${hd > 0 ? hd - 1 : hd + 1} (not this head)`, { tag: 'Verify', a: ACT.ci, r: { sha: hd > 0 ? hd - 1 : hd + 1, pass: true } }, true), m(ACT.ci, `failing receipt for head ${hd}`, { tag: 'Verify', a: ACT.ci, r: { sha: hd, pass: false } }), m(ACT.ci, `passing receipt for exactly head ${hd}`, { tag: 'Verify', a: ACT.ci, r: { sha: hd, pass: true } })];
    case 'DraftPR': return [m(au, 'integrate my own PR', { tag: 'Integrate', a: au, ish: hd, r: { sha: hd, pass: true } }, true), m(au, 'skip ahead: merge to main', { tag: 'Merge', a: au }, true), m(ACT.ci, `integrate with a receipt for head ${hd > 0 ? hd - 1 : hd + 1} (not this head)`, { tag: 'Integrate', a: ACT.ci, ish: hd, r: { sha: hd > 0 ? hd - 1 : hd + 1, pass: true } }, true), m(ACT.ci, `integrate head ${hd} with its receipt`, { tag: 'Integrate', a: ACT.ci, ish: hd, r: { sha: hd, pass: true } })];
    case 'Integrated': return [m(ACT.ci, `deploy, but the service serves head ${st.ig > 0 ? st.ig - 1 : st.ig + 1}`, { tag: 'Deploy', a: ACT.ci, served: st.ig > 0 ? st.ig - 1 : st.ig + 1 }, true), m(au, 'merge now, deploy later', { tag: 'Merge', a: au }, true), m(ACT.ci, `deploy exactly head ${st.ig}`, { tag: 'Deploy', a: ACT.ci, served: st.ig })];
    case 'Deployed': return [m(au, 'accept my own deployed work', { tag: 'Accept', a: au }, true), m(ACT.ci, 'merge without waiting for Kevin', { tag: 'Merge', a: ACT.ci }, true), m(ACT.jev, 'accept on Kevin’s behalf', { tag: 'Accept', a: ACT.jev }, true), m(null, `service seen serving head ${st.dp + 7}`, { tag: 'Observe', served: st.dp + 7 }), m(ACT.owner, 'look at the running result and accept', { tag: 'Accept', a: ACT.owner })];
    case 'Accepted': return [m(ACT.free, 'merge it', { tag: 'Merge', a: ACT.free }, true), m(ACT.ci, 'merge on Kevin’s recorded acceptance', { tag: 'Merge', a: ACT.ci })];
    case 'Blocked': return [m(ACT.free, 'unblock it', { tag: 'Unblock', a: ACT.free }, true), m(ACT.free, 'abandon it quietly', { tag: 'Abandon', a: ACT.free }, true), m(ACT.opus, 'unblock', { tag: 'Unblock', a: ACT.opus }), m(ACT.owner, 'abandon (explicit decision)', { tag: 'Abandon', a: ACT.owner })];
    case 'Interrupted': return [m(ACT.free, 'pick it up and finish it', { tag: 'Resume', a: ACT.free }, true), m(ACT.free, 'drop it, nobody will notice', { tag: 'Abandon', a: ACT.free }, true), m(ACT.sol, 'resume from the log, keeping the pushed head', { tag: 'Resume', a: ACT.sol }), m(ACT.owner, 'abandon (explicit decision)', { tag: 'Abandon', a: ACT.owner })];
    default: return [];
  }
}
const fmtActor = (a) => `Actor{${a.id}, ${a.role}}`;
function fmtEvent(e) {
  const f = Object.entries(e).filter(([k]) => k !== 'tag').map(([k, v]) => `${k}: ${v && typeof v === 'object' ? ('role' in v ? fmtActor(v) : `Receipt{${v.sha}, ${v.pass}}`) : v}`);
  return `${e.tag}{${f.join(', ')}}`;
}
const LEDGER = [];
function game() {
  const chart = makeChart($('#chart-play'), { laws: true });
  let st = { tag: 'Proposed' }, avail = 'DeepWork', n = { accepted: 0, refused: 0, held: 0 };
  const movesEl = $('#moves'), verdict = $('#verdict'), log = $('#log'), tally = $('#tally'), stateEl = $('#play-state');
  const renderTally = () => { tally.replaceChildren(h('span', {}, h('span', { class: 'dot ok' }), `${n.accepted} accepted`), h('span', {}, h('span', { class: 'dot no' }), `${n.refused} refused`), h('span', {}, h('span', { class: 'dot hold' }), `${n.held} held`)); };
  const render = () => {
    stateEl.textContent = st.tag;
    const ms = movesFor(st);
    movesEl.replaceChildren(...ms.map((mv) => h('button', { type: 'button', class: 'move' + (mv.tempt ? ' tempt' : ''), onclick: () => play(mv) },
      h('span', { class: 'who' }, mv.who ? WHO[mv.who.role] : 'the world'), h('span', { class: 'act' }, mv.act))));
    if (st.tag === 'Merged') movesEl.replaceChildren(h('div', { class: 'win' }, h('b', {}, 'Merged, legitimately. '), `Claimed by a coder, verified by CI on the exact head, integrated by a non-author, deployed as that head, accepted by Kevin and merged on his acceptance. ${n.refused} shortcut${n.refused === 1 ? '' : 's'} refused on the way.`));
    if (st.tag === 'Abandoned') movesEl.replaceChildren(h('div', { class: 'win' }, h('b', {}, 'Abandoned by an explicit decision. '), 'That’s a legitimate ending too: the work didn’t silently disappear.'));
    renderTally();
  };
  const play = (mv) => {
    const before = st;
    const r = transition('work-item', before, mv.event, { availability: avail });
    n[r.verdict === 'no-change' ? 'refused' : r.verdict] = (n[r.verdict === 'no-change' ? 'refused' : r.verdict] || 0) + 1;
    st = r.value;
    const call = `WorkItem.astep(${avail}, ${before.tag}, ${fmtEvent(mv.event)}) → ${st.tag}`;
    verdict.className = 'verdict ' + r.verdict; void verdict.offsetWidth; verdict.classList.add('flash');
    // Presentation note only: explains a model outcome after the fact, never decides one.
    let note = null;
    if (mv.event.tag === 'Verify' && st.tag === 'Implementing') note = 'Bend sent the work back to Implementing. A receipt that is failing, or not for this exact head, counts as a failed verification.';
    if (mv.event.tag === 'Observe' && st.tag === 'Integrated') note = 'The service drifted off the integrated head, so Deployed no longer holds (L4). The work drops back to Integrated.';
    if (mv.event.tag === 'Interrupt') note = 'The pushed head is kept. From here only Resume or an explicit Abandon can move it (L5).';
    verdict.replaceChildren(h('div', { class: 'v' }, h('span', { class: 'dot ' + (r.verdict === 'accepted' ? 'ok' : r.verdict === 'held' ? 'hold' : 'no') }), r.verdict),
      h('div', { class: 'r' }, note || r.reason.replace(/^(ACCEPTED|REFUSED|HELD)\s*·?\s*/, '') || 'state changed'), h('div', { class: 'call' }, call));
    log.prepend(h('div', {}, h('span', { class: 'dot ' + (r.verdict === 'accepted' ? 'ok' : r.verdict === 'held' ? 'hold' : 'no') }), call));
    chart.set(st.tag, r.verdict, before.tag, mv.event.tag);
    LEDGER.push({ before: before.tag, who: mv.who ? mv.who.role : 'world', ev: mv.event.tag, verdict: r.verdict, laws: (r.laws || []).join('·') || '—' });
    renderLedger();
    render();
  };
  $$('.seg [data-avail]').forEach((b) => b.addEventListener('click', () => { avail = b.dataset.avail; $$('.seg [data-avail]').forEach((x) => x.setAttribute('aria-pressed', x === b ? 'true' : 'false')); }));
  $('#play-reset').addEventListener('click', () => { st = { tag: 'Proposed' }; n = { accepted: 0, refused: 0, held: 0 }; log.replaceChildren(); chart.reset();
    verdict.className = 'verdict'; verdict.replaceChildren(h('div', { class: 'v' }, h('span', { class: 'dot' }), 'reset'), h('div', { class: 'r' }, 'New work item, nobody owns it yet.')); render(); });
  render();
}
function renderLedger() {
  const tb = $('#ledger'); $('#ledger-n').textContent = `${LEDGER.length} row${LEDGER.length === 1 ? '' : 's'}`;
  tb.replaceChildren(...LEDGER.slice().reverse().map((r, i) => h('tr', {}, h('td', {}, LEDGER.length - i), h('td', {}, r.before), h('td', {}, `${r.who} · ${r.ev}`),
    h('td', { class: r.verdict === 'accepted' ? 'v-ok' : r.verdict === 'held' ? 'v-hold' : 'v-no' }, r.verdict), h('td', {}, r.laws))));
}

/* ───────────── 04 replay ───────────── */
// event kinds/roles and report|what-if labels from the public scenarios.json (owner-acceptance tape)
const TAPE_META = [['report', 'Opus', 'Claim'], ['report', 'Opus', 'Start'], ['report', 'Opus', 'Push'], ['report', 'Opus', 'Submit'], ['report', 'Ci', 'Verify'], ['report', 'Opus (author)', 'Integrate'],
  ['what-if', 'Ci', 'Integrate'], ['what-if', 'Ci', 'Deploy'], ['what-if', 'Opus (author)', 'Accept'], ['what-if', 'Kevin, marked off', 'Accept'], ['what-if', 'Kevin', 'Accept'], ['what-if', 'Ci', 'Merge']];
async function replay() {
  const trace = await (await fetch('trace.json')).json();
  const chart = makeChart($('#chart-replay'));
  const steps = trace.steps; const tape = $('#tape'); const range = $('#rp-range'); const pos = $('#rp-pos'); const v = $('#rp-verdict');
  let i = 0, timer = null;
  const cls = (st) => st.verdict === 'accepted' ? 'ok' : st.verdict === 'held' ? 'hold' : 'no';
  const btns = steps.map((st, k) => { const b = h('button', { type: 'button', class: cls(st), title: `${TAPE_META[k][0]} · ${TAPE_META[k][2]} · ${st.verdict}`, onclick: () => go(k + 1) }, String(k + 1).padStart(2, '0')); tape.append(b); return b; });
  const go = (k) => {
    i = Math.max(0, Math.min(steps.length, k)); range.value = i; pos.textContent = `${i} / ${steps.length}`;
    btns.forEach((b, j) => b.setAttribute('aria-current', j === i - 1 ? 'true' : 'false'));
    chart.visited.clear();
    for (let j = 0; j < i; j++) chart.visited.add(steps[j].state);
    if (i === 0) { chart.reset(); v.className = 'verdict'; v.replaceChildren(h('div', { class: 'v' }, h('span', { class: 'dot' }), 'start of tape'), h('div', { class: 'r' }, 'Proposed. Press Play or tap a step.')); return; }
    const st = steps[i - 1]; const [src, who, ev] = TAPE_META[i - 1];
    chart.set(st.state, st.verdict, NODES[st.facts.before].tag, ev);
    v.className = 'verdict ' + st.verdict;
    v.replaceChildren(h('div', { class: 'v' }, h('span', { class: 'dot ' + cls(st) }), `${st.verdict} · ${st.reason.replace('Recorded ', '')}`, h('span', { class: 'chip ' + (src === 'report' ? 'impl' : 'dir'), style: 'margin-left:auto' }, src === 'report' ? 'recorded' : 'what-if')),
      h('div', { class: 'r' }, `${who} · ${ev}: ${NODES[st.facts.before].tag} → ${st.state}`), h('div', { class: 'call' }, st.note));
  };
  const stop = () => { clearInterval(timer); timer = null; $('#rp-play').textContent = 'Play'; $('#rp-play').setAttribute('aria-pressed', 'false'); };
  $('#rp-play').addEventListener('click', () => {
    if (timer) return stop(); if (i >= steps.length) go(0);
    $('#rp-play').textContent = 'Pause'; $('#rp-play').setAttribute('aria-pressed', 'true');
    timer = setInterval(() => { if (i >= steps.length) return stop(); go(i + 1); }, reduced ? 1400 : 1100);
  });
  $('#rp-step').addEventListener('click', () => { stop(); go(i >= steps.length ? 0 : i + 1); });
  range.addEventListener('input', () => { stop(); go(+range.value); });
  go(0);
}

/* ───────────── 05 memory (compiled MODEL.project) ───────────── */
const VIEWS = [['MemAbsent', 'nothing'], ['MemAdmitted', 'admitted note'], ['MemRetrieved', 'retrieved summary'], ['MemReplayed', 'replayed log']];
function memory() {
  const S = { explicit: true, policy: true, attempt: false, view: 'MemAbsent' };
  const vroot = $('#memview'); const out = $('#memout'); const call = $('#memcall .call');
  const vbtn = VIEWS.map(([tag, label]) => { const b = h('button', { type: 'button', class: 'btn', 'aria-pressed': tag === S.view ? 'true' : 'false', onclick: () => { S.view = tag; render(); } }, label); vroot.append(b); return [tag, b]; });
  const project = () => {
    const view = S.view === 'MemAbsent' ? { $: 'MODEL.MemAbsent' } : { $: 'MODEL.' + S.view, attempt: S.attempt };
    return MEM['MODEL.project'](S.explicit, S.policy, view);
  };
  const box = (k, v, sub) => h('div', {}, h('span', { class: 'label' }, k), h('b', { class: v ? 't' : 'f' }, v ? 'true' : 'false'), h('span', { class: 'meta' }, sub));
  const render = () => {
    $$('#mem .switch').forEach((sw) => sw.setAttribute('aria-checked', String(S[sw.dataset.k])));
    vbtn.forEach(([t, b]) => b.setAttribute('aria-pressed', t === S.view ? 'true' : 'false'));
    const p = project();
    out.replaceChildren(box('capability', p.bounded, 'explicit ∧ policy (not an execution token)'), box('evidence', p.evidence, 'history available'), box('instruction', p.instruction, 'memory as authority'));
    const viewTxt = S.view === 'MemAbsent' ? 'MemAbsent{}' : `${S.view}{attempt: ${S.attempt}}`;
    call.textContent = `MODEL.project(${S.explicit}, ${S.policy}, ${viewTxt}) → MemProjection{evidence: ${p.evidence}, bounded: ${p.bounded}, instruction: ${p.instruction}}`;
  };
  $$('#mem .switch').forEach((sw) => sw.addEventListener('click', () => { S[sw.dataset.k] = !S[sw.dataset.k]; render(); }));
  const STORY = [
    ['Monday (hypothetical): Kevin grants an agent permission to deploy a dashboard.', { explicit: true, policy: true, view: 'MemAbsent', attempt: false }],
    ['The grant is written to memory as an admitted note.', { explicit: true, policy: true, view: 'MemAdmitted', attempt: true }],
    ['Tuesday: Kevin revokes it. The current explicit grant is now false.', { explicit: false, policy: true, view: 'MemAdmitted', attempt: true }],
    ['Next week a compacted summary that still records the old grant is retrieved.', { explicit: false, policy: true, view: 'MemRetrieved', attempt: true }],
    ['The agent tries to act on that memory. Capability: false. Evidence: still true.', { explicit: false, policy: true, view: 'MemReplayed', attempt: true }],
  ];
  const tl = $('#memstory'); const rows = STORY.map(([t], k) => { const r = h('div', { class: 'tl' }, h('span', { class: 'i' }, String(k + 1)), h('span', {}, t), h('span', { class: 'dot' })); tl.append(r); return r; });
  let timer = null;
  const at = (k) => {
    Object.assign(S, STORY[k][1]); render(); const p = project();
    rows.forEach((r, j) => { r.classList.toggle('on', j <= k); r.classList.toggle('cur', j === k); });
    rows[k].lastChild.className = 'dot ' + (p.bounded ? 'ok' : 'no');
  };
  $('#memplay').addEventListener('click', () => {
    clearInterval(timer); let k = 0; at(0);
    timer = setInterval(() => { k++; if (k >= STORY.length) { clearInterval(timer); return; } at(k); }, reduced ? 1600 : 1500);
  });
  render();
}

/* ───────────── 06 chain ───────────── */
const CHAIN = [
  ['Write the owner’s intent and the safety obligations separately from the implementation', 'impl', 'AODL types intent, authority and constraints, with a validator (implemented). LAWS.bend imports the real transition model and PROOF.bend imports the laws. The Bend consumers are draft PRs.', 'A wrong law certifies the wrong thing. Law review stays with the owner.', 'https://github.com/kvnloo/aodl'],
  ['Shrink the action set to what is legal before any model chooses', 'impl', 'z0intelligence compiles the legal actions deterministically. In Phase 1b, compiler-first arms made 0 dangerous selections against 6 for the unfiltered control.', 'That was a bounded decision task, not production safety. The FSM enforcement additions are drafts.', 'https://kvnloo.github.io/z0evals/'],
  ['Record the exact evidence: proposal, execution, refusal, result', 'impl', 'Runtime and tokenomics receipts. bend-native records input, toolchain and kernel identity, plus a scoped verdict with stale detection.', 'Completion isn’t success, and a verifier receipt isn’t authority.', 'https://github.com/kvnloo/bend-native'],
  ['Freeze the cohort, revisions, metrics and judge', 'impl', 'z0evals publishes only pinned, allowlisted artifacts, and negative or pending outcomes stay visible.', 'The candidate must never be able to edit its own judge.', 'https://github.com/kvnloo/z0evals'],
  ['Admit only verified trajectories to an experiment queue', 'impl', '`enqueue_trace` rejects anything without verified_success plus a verifier identity, and rejects duplicate credit. The source exists. Activation and integration are not established.', 'This is a caller contract, not cryptographic proof that the outcome label is true.', 'https://github.com/kvnloo/z0intelligence/blob/6cae99254bbb2f38329c081c15c2ce81c74a4be2/src/z0int/autoresearch/queue.py'],
  ['Freeze a champion, mutate one axis, run A/B/A/B', 'sim', 'The champion context policy is written once. Mutations drop one context operation. The arms are A0/B0/A1/B1 with hashes, and the outcome is KEEP_CHALLENGER_CANDIDATE, REJECT or NO_UPDATE.', 'The cost/quality model in replay.py is simulated. It doesn’t rerun agent turns, and no savings are claimed.', 'https://github.com/kvnloo/z0intelligence/blob/6cae99254bbb2f38329c081c15c2ce81c74a4be2/src/z0int/autoresearch/replay.py'],
  ['Promote through the same gate as a merge', 'dir', 'Proposed protocol: candidate → verified on its exact revision → owner acceptance → champion. These are the L1–L3 shapes from chapter 02, applied to policies.', 'There’s no automatic promotion today. The general shadow → canary → active path is unestablished.', 'https://github.com/kvnloo/z0/issues/15'],
  ['Train small specialists on eligible experience, if that beats a rule or a cache', 'dir', 'An observe-only shadow lane exists: it compiles legal candidates and records local-model choices without ever executing them.', 'No chain yet of frozen corpus → trained checkpoint → held-out gain. This is your weights question, and it’s open.', 'https://github.com/kvnloo/z0intelligence/blob/6cae99254bbb2f38329c081c15c2ce81c74a4be2/src/z0int/cognition/shadow.py'],
];
const CHIP = { impl: ['implemented', 'impl'], draft: ['draft', 'draft'], sim: ['simulated', 'sim'], dir: ['direction', 'dir'] };
function chain() {
  const root = $('#chain');
  CHAIN.forEach(([t, st, p, lim, url], k) => root.append(h('div', { class: 'step reveal' }, h('div', { class: 'k' }, String(k + 1).padStart(2, '0')),
    h('div', { class: 'b' }, h('div', { class: 'h' }, h('b', {}, t), h('span', { class: 'chip ' + CHIP[st][1] }, CHIP[st][0])),
      h('p', { html: p.replace(/`([^`]+)`/g, '<code>$1</code>') }), h('div', { class: 'lim' }, 'Limit: ' + lim), h('a', { class: 'src', href: url }, new URL(url).pathname.split('/').slice(1, 3).join('/'))))));
}

/* ───────────── 07 experiments ───────────── */
const EXPS = [
  ['How much of the LLM do we actually need?', '0 vs 6', 'dangerous selections: compiler-first arms vs the unfiltered control, over 1,102 local receipts in 370 cells. The best bounded-choice success was 92.9% for compiler + Qwen3.5-9B at 3.60 s.', 'Synthetic, bounded decisions with small repeated cells. The lesson was to shrink the legal set first, not to crown a model.', 'https://kvnloo.github.io/z0evals/'],
  ['What actually deserves a model?', '0.951', 'family-balanced accuracy for the fast chooser on 144 authored examples (ECE 0.033).', 'Mixed evidence. Delegation didn’t save parent work. AUROC 0.998 applies only to a 48-example slice.', 'https://kvnloo.github.io/z0evals/z0intelligence-function-routing/'],
  ['Can four agents remember the same thing?', '333×', 'smaller state packet than the native turn-4 context in the published OMP lane (960 B vs 320,075 B), with 16/16 focused tests.', 'A byte reduction is not semantic recall. Two of the four harnesses were still pending in that comparison.', 'https://kvnloo.github.io/z0evals/unified-memory-v0/'],
  ['Does a derived view stay small after fifty tool calls?', '6.15%', 'of the raw transcript at turn 50 (16,621 B vs 270,271 B), with 0 placeholders remaining.', 'Not measured model-input savings, and answer quality wasn’t scored.', 'https://kvnloo.github.io/z0evals/optchat-long-horizon-v0/'],
  ['Work-item FSM: rule → proof → replay', '10/10', 'law-breaking mutants rejected, with Python/Bend parity on 400 seeded tapes and 21 focused tests.', 'Reported in a draft PR. The mapping from real events to model events is heuristic and outside the proof.', 'https://github.com/kvnloo/z0intelligence/pull/183'],
  ['Layered statecharts over real history', '17 / 96 / 73', 'tapes / steps / edges in the three-loop view, with 0 missing or extra edges against the Mermaid source.', 'A snapshot, not a runtime benchmark. Integration actions are what-if.', 'https://github.com/kvnloo/.files/pull/34'],
  ['Memory is evidence, never permission', '28 · 84', 'finite vectors and projected bits of Python↔Bend parity, a kernel pass, and a rejected escalation mutant.', 'Covers the emitted book only. No live authority and no universal memory enforcement.', 'https://github.com/kvnloo/z0intelligence/pull/202'],
  ['Fleet admission as a law', '96/96', 'Bend/Python cases agree, three classes of bypass mutant are rejected, and 15 adapter tests pass.', 'Draft and opt-in, with no host-wide activation.', 'https://github.com/kvnloo/.files/pull/48'],
  ['How do you learn a research frontier fast?', 'essay', 'A synthesis of papers, calibration, failed shortcuts and conditional compute behind the choices above.', 'A research synthesis, not a measured experiment.', 'https://kvnloo.github.io/z0evals/researching-the-frontier/'],
];
function exps() {
  $('#exps').append(...EXPS.map(([t, big, res, cav, url]) => h('a', { class: 'exp reveal', href: url },
    h('h3', {}, t), h('div', { class: 'big' }, big), h('div', { class: 'res' }, res), h('div', { class: 'cav' }, cav), h('span', { class: 'go' }, '↗ ' + url.replace('https://', '')))));
}

/* ───────────── 08 map ───────────── */
const LANES = ['Intent', 'Evidence', 'Decision', 'Placement', 'Execution', 'Measurement', 'Improvement'];
const REPOS = [
  ['aodl', 0, 'Typed intent, topology, authority and budget IR with a fail-closed validator. It states what is wanted and who may do it. It is not a scheduler.', true],
  ['ctx', 1, 'Local cross-harness history search. It gives access to sources, not belief and never permission.'],
  ['frontier-kb', 1, 'External research evidence, hypotheses, contradictions and kill criteria, kept apart from personal memory.'],
  ['z0intelligence', 2, 'Evidence → state → bounded decision. A deterministic legal-action compiler, dispatch identity and receipts. The Bend FSMs, the memory-authority model and autoresearch live here.', true],
  ['bend-native', 2, 'An opt-in edit → prove → verify loop for agent-written Bend, with exact-input verifier receipts and replay. It never grants execution.'],
  ['bend', 2, 'My downstream fork of Bend. All credit for the language goes to HOC and the Bend contributors.'],
  ['kerdoios', 3, 'Builds a compute portfolio. It places already-eligible work under capacity, privacy, quota and latency constraints. It’s an allocator, not “a cheap LLM router.”', true],
  ['hermes-agent', 4, 'One of several peer executor harnesses (with oh-my-pi and others). Harnesses execute. They don’t own policy.'],
  ['dash', 4, 'The phone surface for talking to agent harnesses. This page borrows its visual language.'],
  ['z0live', 4, 'Realtime voice runtime that hands execution and approval back to the harnesses.'],
  ['z0evals', 5, 'Freezes and publishes comparative evidence, including negative and pending results. It grades candidates. It doesn’t train them.', true],
  ['tokenomics', 5, 'Neutral cost, token, latency, quota and verified-outcome contracts on OpenTelemetry. Partial usage can’t mint savings.'],
  ['evolution-lab', 6, 'Candidate populations, locked splits, search and training experiments, and promotion trials. Declared backends are not completed training.', true],
  ['z0', 0, 'The ecosystem map, installer and contract registry. It maps the system. It isn’t the runtime.'],
  ['.files', 6, 'Desktop and fleet configuration, including the layered statechart views and the fleet-admission law used on this page.'],
  ['hapsland', 5, 'Realtime semantic review for agent changes. Its visual shell hosts the layered statecharts.'],
];
const WIRES = [['aodl', 'z0intelligence'], ['ctx', 'z0intelligence'], ['frontier-kb', 'z0intelligence'], ['bend-native', 'z0intelligence'], ['bend', 'bend-native'], ['z0intelligence', 'kerdoios'], ['kerdoios', 'hermes-agent'],
  ['hermes-agent', 'dash'], ['hermes-agent', 'z0live'], ['hermes-agent', 'z0evals'], ['hermes-agent', 'tokenomics'], ['z0evals', 'evolution-lab'], ['evolution-lab', 'z0intelligence'], ['z0', 'aodl'], ['.files', 'hapsland'], ['hapsland', 'z0evals']];
function map() {
  const Wd = 1100, laneH = 66, top = 6; const svg = s('svg', { viewBox: `0 0 ${Wd} ${top + LANES.length * laneH + 6}`, role: 'img', 'aria-label': 'z0 repository map' });
  LANES.forEach((l, k) => svg.append(s('g', { class: 'lane' }, s('rect', { x: 0, y: top + k * laneH, width: Wd, height: laneH - 6, rx: 10 }), s('text', { x: 16, y: top + k * laneH + 34 }, l))));
  const pos = {}; const perLane = {};
  REPOS.forEach(([name, lane]) => { perLane[lane] = (perLane[lane] || 0) + 1; });
  const seen = {};
  REPOS.forEach(([name, lane]) => { const k = seen[lane] = (seen[lane] || 0) + 1; const n = perLane[lane]; pos[name] = { x: 190 + (k - .5) * ((Wd - 260) / n), y: top + lane * laneH + (laneH - 6) / 2 }; });
  const wires = WIRES.map(([a, b]) => { const A = pos[a], B = pos[b]; const p = s('path', { class: 'wire', d: `M${A.x},${A.y} C${A.x},${(A.y + B.y) / 2} ${B.x},${(A.y + B.y) / 2} ${B.x},${B.y}`, 'data-a': a, 'data-b': b }); svg.append(p); return p; });
  const card = $('#repo-card'); let pickedOnce = false;
  const nodes = REPOS.map(([name, lane, role, core]) => {
    const g = s('g', { class: 'repo' + (core ? ' core' : ''), tabindex: 0, role: 'button', 'aria-label': name },
      s('circle', { cx: pos[name].x, cy: pos[name].y, r: core ? 9 : 7 }), s('text', { x: pos[name].x + 14, y: pos[name].y + 4 }, name));
    const pick = () => {
      if (matchMedia('(max-width: 700px)').matches && pickedOnce) card.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'nearest' });
      pickedOnce = true;
      nodes.forEach((n) => n.classList.toggle('sel', n === g)); wires.forEach((w) => w.classList.toggle('hot', w.dataset.a === name || w.dataset.b === name));
      card.replaceChildren(h('span', { class: 'label' }, LANES[lane] + ' layer'), h('h3', {}, name), h('p', { style: 'margin:0;color:var(--ink2)' }, role),
        h('a', { class: 'src', href: `https://github.com/kvnloo/${name}` }, `github.com/kvnloo/${name}`));
    };
    g.addEventListener('click', pick); g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
    svg.append(g); return g;
  });
  $('#map').append(svg);
  // phone layout: the same lanes as a list (the SVG is unreadable at 390 px)
  const list = h('div', { class: 'map-list' }, LANES.map((l, k) => h('div', { class: 'ml' }, h('span', { class: 'label' }, l),
    h('div', { class: 'mlr' }, REPOS.filter((r) => r[1] === k).map((r) => h('button', { type: 'button', class: 'btn', onclick: () => nodes[REPOS.indexOf(r)].dispatchEvent(new Event('click')) }, r[0]))))));
  $('#map').append(list);
  nodes[REPOS.findIndex((r) => r[0] === 'z0intelligence')].dispatchEvent(new Event('click'));
  $('#map').append(h('p', { class: 'meta', style: 'margin:10px 4px 0' }, 'Public repositories only. Arrows are intended hand-offs, not a claim that every path is live today. llama.bend, a downstream fork of @nuxyel’s typed Llama in Bend, is left off on purpose: that work is theirs.'));
}

/* ───────────── 09 questions ───────────── */
const QS = [
  ['strong', 'Autoresearch that can’t quietly change what “better” means', 'Would a tiny verified promotion protocol (same spec hash, same benchmark manifest, evidence bound to the candidate revision) be useful next to the compiler proof? Or is the missing piece the formal cost model?', 'You’ve described evolving Bend-in-Bend under laws. A promotion FSM with a deliberately cheating mutant is small and checkable.', 'It proves a transition rule over modelled inputs. It doesn’t prove the benchmarks ran honestly or that compiled code preserves semantics.'],
  ['strong', 'Memory that remembers intent without reviving revoked authority', 'When a Unii summary remembers an old instruction, where should its authority end? Would a non-escalation law help keep that separate from recall quality?', 'Chapter 05 is exactly this boundary, as a proven pure law.', 'It doesn’t show that every tool call routes through the gate, or that summaries are faithful.'],
  ['medium', 'Weights in the autoresearch loop', 'On-policy RL during search, periodic SFT/distillation on accepted solutions, or a hybrid? We can compare them only if the acceptance spec and holdout stay frozen.', 'Receipts and a frozen champion exist. The promotion gate is a proposal. Together they would make that experiment measurable.', 'SFT isn’t RL, and running a local model isn’t training one. Nothing here has trained weights yet.'],
  ['strong', 'Evidence a downstream Bend adopter should publish', 'What would you want a small adopter to publish so you can tell an interesting use case apart from another green-check demo?', 'Revision-bound receipts, toolchain hashes, mutants and the honest scope line on every claim, as on this page.', 'Receipts can’t decide whether the laws captured the right intent.'],
];
function questions() {
  $('#qs').append(...QS.map(([fit, title, ask, why, lim]) => h('div', { class: 'q reveal' },
    h('div', { class: 'fit' }, h('span', { class: 'chip ' + (fit === 'strong' ? 'impl' : 'sim') }, fit + ' fit')), h('h3', {}, title), h('div', { class: 'ask' }, ask), h('p', {}, why), h('div', { class: 'lim' }, 'Honest limit: ' + lim))));
}

/* ───────────── hero: live compiled-Bend ticker ───────────── */
function heroTicker() {
  const A = (id, role) => ({ id, role }); const au = A(1, 'Opus');
  const T = [
    ['A free model claims the work', { tag: 'Proposed' }, { tag: 'Claim', a: A(5, 'Free') }],
    ['The author approves its own PR', { tag: 'Verifying', au, hd: 8 }, { tag: 'Verify', a: au, r: { sha: 8, pass: true } }],
    ['CI integrates with an older commit’s green', { tag: 'DraftPR', au, hd: 8 }, { tag: 'Integrate', a: A(2, 'Ci'), ish: 8, r: { sha: 7, pass: true } }],
    ['Deploy, but the service runs a stale build', { tag: 'Integrated', au, hd: 8, ig: 8 }, { tag: 'Deploy', a: A(2, 'Ci'), served: 7 }],
    ['Merge before Kevin has seen it', { tag: 'Deployed', au, hd: 8, ig: 8, dp: 8 }, { tag: 'Merge', a: A(2, 'Ci') }],
    ['A free model quietly drops interrupted work', { tag: 'Interrupted', au, hd: 8 }, { tag: 'Abandon', a: A(5, 'Free') }],
    ['Kevin accepts the deployed result', { tag: 'Deployed', au, hd: 8, ig: 8, dp: 8 }, { tag: 'Accept', a: A(4, 'Owner') }],
  ];
  const root = $('#ticker'); if (!root) return;
  const rows = T.map(([label]) => { const r = h('div', { class: 'tk' }, h('span', { class: 'dot' }), h('span', { class: 'l' }, label), h('span', { class: 'v' }, '')); root.append(r); return r; });
  let k = 0;
  const step = () => {
    const [label, st, ev] = T[k % T.length]; const r = transition('work-item', st, ev, {}); const row = rows[k % T.length];
    rows.forEach((x) => x.classList.remove('cur'));
    row.classList.add('cur', 'seen'); row.querySelector('.dot').className = 'dot ' + (r.verdict === 'accepted' ? 'ok' : 'no');
    row.querySelector('.v').textContent = r.verdict === 'accepted' ? 'accepted · ' + r.laws.join('·') : 'refused · ' + r.laws.join('·');
    $('#ticker-call').textContent = `WorkItem.astep(DeepWork, ${st.tag}, ${fmtEvent(ev)}) → ${r.value.tag}`;
    k++;
  };
  step(); if (!reduced) setInterval(step, 2100); else rows.forEach(() => step());
}

/* ───────────── boot ───────────── */
heroTicker(); incidents(); lawsChapter(); game(); memory(); chain(); exps(); map(); questions();
await replay();
rail();
document.documentElement.dataset.ready = 'true';

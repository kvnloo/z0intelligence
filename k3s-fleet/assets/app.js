// z0 fleet handoff for Advith. Plain data plus small interactions; nothing here talks to the fleet.
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
const GH = 'https://github.com/kvnloo/';
const link = (path, label) => h('a', { class: 'src', href: GH + path }, label);

/* ───────────── rail + reveal (same as the Bend story) ───────────── */
function rail() {
  const links = $$('.rail a'); const pill = $('.rail .pill');
  const place = (a) => {
    links.forEach((l) => l.setAttribute('aria-current', l === a ? 'true' : 'false'));
    if (matchMedia('(max-width: 900px)').matches) { pill.style.left = a.offsetLeft + 'px'; pill.style.top = ''; const r = a.parentElement; r.scrollTo({ left: a.offsetLeft - r.clientWidth / 2 + a.offsetWidth / 2, behavior: 'smooth' }); }
    else { pill.style.top = a.offsetTop + 'px'; pill.style.left = ''; }
  };
  const byId = Object.fromEntries(links.map((a) => [a.dataset.ch, a]));
  place(links[0]);
  const io = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting && byId[e.target.id]) place(byId[e.target.id]); }, { rootMargin: '-45% 0px -50% 0px' });
  $$('[data-chapter]').forEach((c) => io.observe(c));
  addEventListener('resize', () => place(links.find((l) => l.getAttribute('aria-current') === 'true') || links[0]));
  const rv = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); rv.unobserve(e.target); } }), { rootMargin: '0px 0px -8% 0px' });
  $$('.reveal').forEach((el) => rv.observe(el));
}

/* ───────────── 00 outcomes ───────────── */
const OUT = [
  ['ok', 311, 'exit 0', 'Succeeded.', 'This is the number to grow, measured per verified job, not per attempt.'],
  ['no', 192, 'exit 1', 'Real test failures.', 'Not a capacity problem: these are the code being wrong, and they should stay failures.'],
  ['hold', 41, 'exit 75 (approx.)', 'Held: no room.', 'Admission said no and the job waited or retried every 60 s. It\'s a home-made queue signal, and it hides which gate said no.'],
  ['prov', 14, 'exit 127', 'Missing tool.', 'pnpm, corepack, cargo or bunx was absent for the job user, or a native addon was missing. Toolchain drift (failure 7).'],
  ['faint', 66, 'everything else', 'OOM kills, signals, unclassified.', 'Includes memcg OOM kills that surfaced as a plain SIGTERM 143, so the real reason was hidden (failure 4).'],
];
function outcomes() {
  const bar = $('#outbar'); const det = $('#outdetail'); const total = OUT.reduce((a, o) => a + o[1], 0);
  const show = (o) => {
    $$('.band', bar).forEach((b) => b.classList.toggle('on', b.dataset.k === o[2]));
    det.replaceChildren(h('div', { class: 'od-n' }, h('span', { class: `dot ${o[0]}` }), String(o[1])),
      h('div', {}, h('b', {}, `${o[2]}: ${o[3]}`), h('p', { class: 'meta' }, o[4])));
  };
  OUT.forEach((o) => {
    const b = h('button', { type: 'button', class: `band ${o[0]}`, role: 'listitem', 'data-k': o[2], style: `flex:${o[1]}`, 'aria-label': `${o[1]} jobs, ${o[2]}: ${o[3]}`,
      onmouseenter: () => show(o), onfocus: () => show(o), onclick: () => show(o) },
    o[1] / total > 0.06 ? h('span', {}, String(o[1])) : null);
    bar.append(b);
  });
  show(OUT[2]);
}

/* ───────────── 01 shadow-network toy + resource types ───────────── */
function shadow() {
  const r = $('#sd'); const v = $('#sd-v');
  const paint = () => {
    const x = Number(r.value); const live = x >= 95;
    v.textContent = x.toFixed(1);
    $('#sd-slm').classList.toggle('live', live);
    $('#sd-a2').classList.toggle('dim', !live);
    $('#sd-slmt').textContent = live ? 'takes real traffic' : 'mirror only';
    $('#sd-jev').textContent = live ? 'falls back + audits' : 'answers 100%';
    $('#sd-say').textContent = live
      ? `At ${x.toFixed(1)}%, it clears the gate and starts taking real traffic for this one task, with Jev kept as fallback and auditor. At scale this step should be an analysis run, like a progressive-delivery canary, not a threshold crossing once.`
      : `In shadow at ${x.toFixed(1)}%: it sees every request and answers none. Its answers are only scored against Jev's, which costs eval compute on every request.`;
  };
  r.addEventListener('input', paint); paint();
  const TYPES = [
    ['LLM providers', 'quotas, free tiers, rate limits', 'Not a Kubernetes resource today. Can a scheduler bin-pack it? (question 3)'],
    ['Devices', 'CPU, RAM, GPU, disk, thermal', 'The only kind Kubernetes models natively, and only partly: there\'s nothing for thermal.'],
    ['Intelligence units', 'deterministic code, Jev, SLMs, frontier models', 'Cheapest first. A job should use the dumbest unit that passes its eval.'],
  ];
  $('#types').append(...TYPES.map(([t, d, n], i) => h('div', { class: 'card type' }, h('div', { class: 'type-k' }, `0${i + 1}`), h('h3', {}, t), h('div', { class: 'meta' }, d), h('p', {}, n))));
}

/* ───────────── 02 fleet ───────────── */
const NODES = [
  ['k3s control plane', 'the daily desktop', [['GPU', 'RTX 3080 Ti, 12 GB, shared with ComfyUI'], ['role', 'control plane + GPU node']], 'IO pressure (PSI) hit ~80% that night; load ~39.', 'warn', 'It is also the machine a human is typing on.'],
  ['k3s worker', 'the only build node', [['CPU', 'i7-4870HQ, 4 cores / 8 threads'], ['RAM', '16 GB'], ['disk', '317 GB btrfs']], 'Worn thermal paste: 92–98 °C under load.', 'danger', 'About 20 tenants share it.'],
  ['host-lane workers', 'two Android phones', [['runtime', 'Linux in PRoot on Android'], ['missing', 'cgroups, netlink, systemd']], 'Can\'t join k3s: no kubelet without cgroups.', 'info', 'Battery and thermal gates; jobs sized at 512 MiB.'],
  ['candidates', 'not enrolled yet', [['cloud', 'Oracle free-tier ARM VM'], ['PC', 'a desktop with a 2080 Super']], 'Would make this a 3–4 node cluster.', 'faint', 'Worth it before or after Kueue? (question 1)'],
];
function fleet() {
  $('#nodes').append(...NODES.map(([role, what, specs, real, tone, note]) => h('div', { class: 'card node reveal' },
    h('div', { class: 'label' }, role), h('h3', {}, what),
    h('dl', {}, ...specs.flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v)])),
    h('div', { class: `real ${tone}` }, h('span', { class: `dot ${tone === 'danger' ? 'no' : tone === 'warn' ? 'hold' : tone}` }), real),
    h('div', { class: 'meta' }, note))));
  const PATHS = {
    today: [['agent lead', 'one of ~20 submitting concurrently', 'on'], ['fleet-build', 'controller-side admission: heat, memory, disk', 'on'], ['exit 75 if no room', 'retry every 60 s; the reason is opaque', 'warn'], ['systemd-run scope', 'on the build laptop: MemoryMax + CPUQuota', 'on'], ['job log', 'into the steward ledger (.files#46)', 'on']],
    k8s: [['agent lead', '', 'on'], ['Job with queue-name label', 'created suspended', 'off'], ['LocalQueue → ClusterQueue', 'factory / lanes / z0, installed but empty', 'off'], ['Kueue admission', 'quota, fair share, preemption: unused', 'off'], ['pod on a flavor', 'restricted pods, per-node ResourceFlavors', 'off']],
  };
  const SAY = { today: 'Everything real runs on this path. It works, but fair share, priority and preemption are being re-invented in shell.', k8s: 'This path is installed and smoke-tested, but no production build runs on it yet. Should it? That is question 1.' };
  const draw = (k) => {
    $$('[data-path]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.path === k)));
    $('#path').replaceChildren(...PATHS[k].map(([t, d, st]) => h('li', { class: st }, h('b', {}, t), d ? h('span', { class: 'meta' }, d) : null)));
    $('#path-say').textContent = SAY[k];
  };
  $$('[data-path]').forEach((b) => b.addEventListener('click', () => draw(b.dataset.path))); draw('today');
  const OBS = [['metrics-server', true], ['Prometheus', false], ['kube-state-metrics', false], ['descheduler', false], ['node-problem-detector', false]];
  $('#obs').append(...OBS.map(([n, ok]) => h('li', { class: ok ? 'ok' : 'no' }, h('span', { class: `dot ${ok ? 'ok' : ''}` }), n, h('span', { class: 'meta' }, ok ? 'installed' : 'not installed'))));
}

/* ───────────── 03 eviction toy + failures ───────────── */
function evict() {
  const DISK = 317, START = 0.09, DU = 32, REAL = 4;
  const st = { acct: 'btrfs' };
  const t = $('#ev-t'), r = $('#ev-r');
  const paint = () => {
    const tp = Number(t.value), rp = Number(r.value);
    $('#ev-to').textContent = `${tp}%`; $('#ev-ro').textContent = `${rp}%`;
    $$('[data-acct]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.acct === st.acct)));
    const freed = st.acct === 'du' ? DU : REAL;
    const free0 = DISK * START, free1 = free0 + freed, trig = DISK * tp / 100, clear = DISK * (tp + rp) / 100;
    const scale = (gb) => `${Math.min(100, (gb / (DISK * 0.3)) * 100)}%`; // view: the first 30% of the disk
    $('#disk-free').style.width = scale(free1); $('#disk-used').style.width = scale(free0);
    $('#disk-trig').style.left = scale(trig); $('#disk-clear').style.left = scale(clear);
    const cleared = free1 >= clear;
    $('#disk').classList.toggle('cleared', cleared);
    $('#ev-read').replaceChildren(
      h('div', { class: `verdict ${cleared ? 'ok' : 'no'}` }, cleared ? 'PRESSURE CLEARS' : 'STILL UNDER PRESSURE · BUILDS PAUSED'),
      h('p', {}, `Free after cleanup: ${free1.toFixed(1)} GB (${(free1 / DISK * 100).toFixed(1)}%). Pressure clears at ${clear.toFixed(1)} GB (${tp + rp}% free).`,
        cleared ? '' : ` ${(clear - free1).toFixed(1)} GB short.`),
      h('p', { class: 'meta' }, st.acct === 'du'
        ? 'Counting deletions the way du reports them, which is what the cleanup thought it achieved.'
        : 'Counting what btrfs actually returned. zstd-compressed extents free far less than their apparent size.'));
  };
  [t, r].forEach((i) => i.addEventListener('input', paint));
  $$('[data-acct]').forEach((b) => b.addEventListener('click', () => { st.acct = b.dataset.acct; paint(); }));
  $$('[data-preset]').forEach((b) => b.addEventListener('click', () => { t.value = 10; r.value = b.dataset.preset === 'before' ? 10 : 2; paint(); }));
  paint();
}
const FAILS = [
  ['DiskPressure that wouldn\'t clear', 'evictionMinimumReclaim was 10% on top of the 10% threshold, so pressure only cleared at 20% free on a 317 GB disk, and every build paused. btrfs compression made du overstate what deleting would free.', 'A 2% reclaim drop-in (try it above).', 'Image GC thresholds and eviction tuned per disk, with free space measured the way the filesystem frees it.'],
  ['Thermal', 'A software gate paused the build laptop at 95 °C.', 'The owner now lets the CPU\'s own PROCHOT throttling protect it.', 'Sensor-derived node conditions or taints (node-problem-detector custom plugin?), so the scheduler avoids a hot node instead of a shell gate pausing it.'],
  ['One build node, ~20 tenants', 'Load hit 13.6 on 8 threads. The flock-based "quiet window" lock starved exclusive jobs for 30+ minutes.', 'Added a turnstile.', 'That\'s fair share, priority and preemption reinvented badly. Kueue cohorts, priority classes and preemption already do it.'],
  ['tmpfs /tmp filled RAM', 'A 7.8 GB tmpfs at 100% on a 16 GB box became memory pressure. Scopes were memcg-OOM-killed, and fleet reported plain SIGTERM 143, hiding the real reason.', 'Traced the 143s back to the OOM kills by hand.', 'Memory-backed scratch counted against the job\'s own memory limit, ephemeral-storage limits, and OOMKilled surfaced as the termination reason.'],
  ['No learned right-sizing', '`npm ci` OOMs at 1.5 GiB. One full TypeScript typecheck won\'t fit in 6 GiB, so it went to a hosted CI runner. Requests are guesses.', 'A per-job ledger exists, but nothing feeds it back.', 'A VPA-style recommender (in recommend-only mode) fed by the job ledger, maybe mapped onto Kueue flavors.'],
  ['Control plane on the workstation', 'Agents, local smoke tests, IO-heavy model downloads and a GPU benchmark all compete with the human at the desk.', 'CPU and IO weights for agent work, set by hand.', 'system-reserved / kube-reserved and IO isolation for a desktop node, or move the control plane off it.'],
  ['Toolchain drift on workers', 'Missing pnpm, corepack, cargo and bunx for the job user, plus a missing native addon: all exit 127.', 'Pinned toolchain installs per node.', 'Container images that pin the toolchain, which is the main thing a k8s Job buys over the host lane.'],
  ['Phones can\'t be k8s nodes', 'PRoot means no kubelet.', 'Host-lane workers with battery and thermal gates.', 'virtual-kubelet, or an agent-shim pattern? We don\'t know if either is sane here.'],
  ['GPU sharing', 'The 3080 Ti has no MIG. A voice-model benchmark (Moshi, or an STT → SLM → TTS cascade) would share it with ComfyUI and the desktop.', 'No sharing policy yet.', 'A time-slicing policy, and some way to keep the human\'s display responsive.'],
];
function fails() {
  $('#fails').append(...FAILS.map(([t, what, did, k8s], i) => h('div', { class: 'card fail reveal' },
    h('div', { class: 'fail-head' }, h('span', { class: 'fail-n' }, String(i + 1)), h('h3', {}, t)),
    h('p', {}, what),
    h('div', { class: 'fail-row' }, h('span', { class: 'label' }, 'what we did'), h('span', {}, did)),
    h('div', { class: 'fail-row guess' }, h('span', { class: 'label' }, 'k8s answer? (our guess)'), h('span', {}, k8s)))));
}

/* ───────────── 04 areas ───────────── */
const AREAS = [
  ['Kueue done right', 'Cohorts and fair sharing across lanes, priority classes (interactive vs batch vs eval replay), preemption, and admission checks against learned resource profiles.', [['.files/pull/41', '.files#41 live fleet']]],
  ['Right-sizing from history', 'We already have a per-job ledger: 624 jobs → 16 findings. How should it feed requests and limits (a VPA-like loop) and Kueue flavors?', [['.files/pull/46', '.files#46 cluster steward']]],
  ['A workstation-safe control plane', 'IO and CPU isolation for the desktop, or moving the control plane off it entirely.', [['.files/pull/48', '.files#48 admission never falls back to the desktop']]],
  ['Heterogeneous capacity', 'A GPU time-slicing policy. A virtual-kubelet or extended-resource story for phones and cloud quotas, i.e. LLM provider quota as a schedulable resource.', [['kerdoios/issues/50', 'kerdoios#50'], ['kerdoios/issues/51', 'kerdoios#51 quota ledger']]],
  ['Observability', 'The minimal kube-state-metrics, node-problem-detector and descheduler setup worth running on a 2-node cluster.', [['z0intelligence/issues/131#issuecomment-6356877382', 'Z01-K8S-OSS research']]],
  ['Scaling the shadow network', 'What you\'d build to run eval-gated model promotion at production scale: Argo Rollouts-style analysis, or batch Job patterns.', [['z0intelligence/issues/133', 'z0intelligence#133 capacity snapshot']]],
];
function areas() {
  $('#areas').append(...AREAS.map(([t, d, ls], i) => h('div', { class: 'card area reveal' }, h('div', { class: 'type-k' }, `0${i + 1}`), h('h3', {}, t), h('p', {}, d), h('div', { class: 'links' }, ...ls.map(([p, l]) => link(p, l))))));
}

/* ───────────── 05 questions ───────────── */
const QS = [
  ['Given our workload (bursty, short, mostly CPU, a few GPU), should builds and evals move from the host-lane wrapper onto Kueue Jobs now, or stay host-lane until we have more nodes?',
    'An opt-in hybrid, not a cutover: keep fleet-build as the entry point, and add a Kueue adapter only for bounded, hermetic, CPU-only verification once node health is fixed. Zero production k8s build jobs until then.', 'z0intelligence/issues/131#issuecomment-6356821555', 'Z01-K8S-MIGRATE'],
  ['What\'s your default eviction and reserved config for a k3s node that is also someone\'s desktop?',
    'Reconcile kubelet eviction and image GC with physical btrfs accounting first. We only have the 2% reclaim fix; nothing principled for system-reserved / kube-reserved yet.', 'z0intelligence/issues/131#issuecomment-6356877382', 'Z01-K8S-OSS'],
  ['How would you model "LLM provider quota" and "free-tier tokens" so the scheduler can bin-pack across them alongside CPU/GPU?',
    'Open. The current direction keeps a provider quota ledger in Kerdoios and projects it as capacity offers, outside Kubernetes.', 'kerdoios/issues/51', 'kerdoios#51'],
  ['What\'s the smallest metrics stack you\'d trust on a 2-node cluster?',
    'Narrowly scoped kube-state-metrics, then one single-node VictoriaMetrics only after a disk/IO budget is proved. No descheduler yet; node-problem-detector later, as a targeted pilot.', 'z0intelligence/issues/131#issuecomment-6356877382', 'Z01-K8S-OSS'],
  ['Where would you draw the line between learned placement (our steward and Kerdoios) and native k8s primitives?',
    'The learned layer stays advisory: it ranks already-legal offers and emits read-only lessons. Admission stays in fleet-build and Kueue, and an LLM never gets scheduling authority.', 'z0intelligence/issues/131#issuecomment-6356895926', 'Z01-K8S-STEWARD'],
];
function questions() {
  $('#qs').append(...QS.map(([q, lean, p, l], i) => h('div', { class: 'card q reveal' },
    h('div', { class: 'q-n' }, String(i + 1)),
    h('div', {}, h('h3', {}, q),
      h('details', {}, h('summary', {}, 'what our agents\' research leaned toward'), h('div', {}, h('p', {}, lean), link(p, l)))))));
}

/* ───────────── 06 pointers + brief ───────────── */
const PTRS = [
  ['Fleet code and config · kvnloo/.files', [['.files/pull/41', '#41 live fleet: admission, eviction, thermal'], ['.files/pull/46', '#46 cluster steward: job ledger, analyzer, capacity projection'], ['.files/pull/44', '#44 registry and health integration'], ['.files/pull/48', '#48 Bend2 proof that fleet admission never falls back to the desktop']]],
  ['Capacity and placement · kvnloo/kerdoios', [['kerdoios/issues/50', '#50 resource-aware SLM placement'], ['kerdoios/issues/51', '#51 provider quota ledger'], ['kerdoios/pull/57', 'PR #57 capacity projection']]],
  ['Architecture · kvnloo/z0intelligence', [['z0intelligence/issues/131', '#131 RFC: federated capacity plane'], ['z0intelligence/issues/133', '#133 capacity snapshot']]],
  ['Research our agents already ran (comments on #131)', [['z0intelligence/issues/131#issuecomment-6356895926', 'Z01-K8S-STEWARD'], ['z0intelligence/issues/131#issuecomment-6356877382', 'Z01-K8S-OSS'], ['z0intelligence/issues/131#issuecomment-6356843385', 'Z01-K8S-LEARN'], ['z0intelligence/issues/131#issuecomment-6356821555', 'Z01-K8S-MIGRATE']]],
];
function pointers() {
  $('#ptrs').append(...PTRS.map(([t, ls]) => h('div', { class: 'ptr' }, h('div', { class: 'label' }, t), h('ul', {}, ...ls.map(([p, l]) => h('li', {}, link(p, l)))))));
  const brief = [
    '# z0 fleet: review brief',
    '',
    'Goal: advise on scheduling, capacity and scaling policy for thousands of short, bursty AI jobs',
    '(replays, evals, SFT runs, builds, tests) submitted by ~20 agents, on a 2-node k3s cluster plus',
    'two PRoot phone workers. k3s itself is healthy; the placement layer above it is the problem.',
    '',
    'Facts (owner-reported, one night, 624 job logs):',
    '- Outcomes: 311 exit 0, 192 exit 1, ~41 exit 75 (held/no room), 14 exit 127, rest OOM/signals.',
    '- Kueue installed with ClusterQueues factory, lanes, z0, all empty; jobs run via fleet-build ->',
    '  systemd-run scopes on the single build node. Only metrics-server installed.',
    '- Control plane is also the daily desktop (RTX 3080 Ti, no MIG). Build node: i7-4870HQ, 16 GB,',
    '  317 GB btrfs, 92-98 C under load.',
    '',
    'Read first:',
    ...PTRS.flatMap(([t, ls]) => ls.map(([p, l]) => `- ${GH}${p}  (${l})`)),
    '',
    'Answer:',
    ...QS.map(([q], i) => `${i + 1}. ${q}`),
    '',
    'Constraints: the desktop must stay responsive; phones cannot run a kubelet; heavy work stays off',
    'the desktop; learned placement may advise but never holds admission authority.',
  ].join('\n');
  $('#brieftext').textContent = brief;
  $('#copy').addEventListener('click', async (e) => {
    const b = e.currentTarget;
    try { await navigator.clipboard.writeText(brief); b.textContent = 'Copied'; }
    catch { getSelection().selectAllChildren($('#brieftext')); b.textContent = 'Selected: press copy'; }
    setTimeout(() => { b.textContent = 'Copy brief'; }, 1800);
  });
}

outcomes(); shadow(); fleet(); evict(); fails(); areas(); questions(); pointers(); rail();

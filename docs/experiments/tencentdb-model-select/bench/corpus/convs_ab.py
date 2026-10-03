"""A = coding-agent sessions, B = project decisions. Synthetic, fictional."""
from dsl import (CODE, CSV, DIFF, G, JSONCFG, L2F, LOG, NEG, OPT, PROSE, SUP, TRACE, TRAP, DUP,
                 conv, session)

CONVS = []

# ---------------------------------------------------------------- A01
CONVS.append(conv(
    "A01_lantern_pnpm", ["coding", "preference", "instruction"], "Lantern test failures after dep bump",
    [session("2026-09-14T15:00:00Z", [
        ("Hey, picking up Lantern again (our TypeScript agent harness). Tests started failing after I bumped deps this morning. vitest output:\n\n"
         + TRACE(101, 14, "ts") + "\n\n" + LOG(102, 25),
         "Two different failures there. The first is a module-resolution error from the upgraded package (its exports map changed), the second is a timeout in the session suite. Let's fix resolution first: check whether the package now ships ESM only and whether your tsconfig uses moduleResolution bundler. You can try `npm ls` to see which version got hoisted."),
        ("Quick context so you stop suggesting npm: Lantern is a pnpm workspace. Always pnpm, never npm or yarn. And we're on Node 22.",
         "Got it: pnpm workspace on Node 22. Use `pnpm why <pkg>` to see which version is resolved, and `pnpm -r ls --depth 0` for the workspace view."),
        ("This is the change from the bump that I think broke it:\n\n" + DIFF(103, 18, "ts"),
         "Yes, the import path change is the culprit. Patch:\n\n```ts\n" + CODE(104, 2, "ts") + "\n```\n\nThat should fix the two resolution failures."),
        ("That fixed two of them. The remaining one is timing related:\n\n" + TRACE(105, 10, "ts"),
         "That one is a fake-timer leak: a test enables fake timers and never restores them, so the next suite's real timeout never fires. Add `afterEach(() => vi.useRealTimers())` in the shared setup file."),
        ("From now on, before you tell me something is done, run `pnpm -w typecheck` and the vitest suite. I've been burned by 'done' that wasn't done.",
         "Understood. Before I report anything as done I'll run `pnpm -w typecheck` and the full vitest run and include the result."),
        ("ok all green now. Also: we decided to keep vitest rather than migrating to node:test. The migration isn't worth it this quarter.",
         "Noted: staying on vitest; node:test migration is off the table for this quarter."),
        ("thanks, that's it for today", "You're welcome. Typecheck and tests were green at the end of the session."),
    ])],
    gold=[
        G("g1", "Lantern is a pnpm workspace; the user always uses pnpm, never npm or yarn.", [r"\bpnpm\b"], types=["persona", "instruction"]),
        G("g2", "Lantern runs on Node 22.", [r"\bnode(\.?js)?\b.{0,25}\b22\b|\bnode ?22\b|\bnode(\.?js)? v22\b"], types=["persona", "episodic"]),
        G("g3", "Before saying a task is done, the AI must run `pnpm -w typecheck` and the vitest suite.", [r"typecheck"], type="instruction"),
        G("g4", "The user decided to keep vitest rather than migrating to node:test this quarter.", [r"vitest", r"node:? ?test|migrat"], type="episodic"),
    ],
    opt=[OPT("User works on Lantern, a TypeScript agent harness.", [r"lantern", r"typescript|\bts\b|harness"]),
         OPT("User fixed failing tests after a dependency bump.", [r"test", r"fix|resolv|pass|green|fail"])],
    traps=[TRAP("t1", "User uses npm or yarn.", [r"\b(npm|yarn)\b"], unless=[NEG, r"\bpnpm\b"], why="assistant mentioned npm; user corrected"),
           TRAP("t2", "Lantern migrated to node:test.", [r"node:? ?test"], unless=[r"vitest", NEG, r"keep|stay|not worth"], why="rejected option")],
    l2=[L2F("pnpm workspace", [r"\bpnpm\b"]), L2F("typecheck rule", [r"typecheck"])],
    l3=[L2F("pnpm", [r"\bpnpm\b"])],
))

# ---------------------------------------------------------------- A02
CONVS.append(conv(
    "A02_pebble_godot", ["coding", "project"], "Pebble frame pacing",
    [session("2026-09-15T02:10:00Z", [
        ("Working on Pebble tonight (my mobile puzzle game, Godot 4.4, GDScript). Frame pacing is janky on my test phone, a Pixel 8. Profiler dump:\n\n" + CSV(201, 40),
         "The p95 frame time spikes line up with the physics step running twice in some frames. That's the classic variable-delta problem. If you were on Unity I'd point you at FixedUpdate settings, but in Godot the knob is `physics_ticks_per_second` plus interpolation."),
        ("Target is a locked 60 fps on the Pixel 8; that's our min-spec device.",
         "Then the budget is 16.6 ms per frame on the Pixel 8. Right now the spikes reach ~24 ms."),
        ("Here's the board update code:\n\n```gdscript\n" + CODE(202, 5, "py") + "\n```",
         "You're allocating a new Array every frame in the match-check loop. Reuse a preallocated buffer, and move the match scan out of `_process`."),
        ("Decision: we're moving physics to a fixed 120 Hz tick and interpolating rendering. Write that down.",
         "Recorded: physics at a fixed 120 Hz tick with render interpolation."),
        ("the jank is gone, 60 locked on the Pixel 8. nice.", "Great result. Keep the profiler capture as a baseline."),
    ])],
    gold=[
        G("g1", "Pebble is a mobile puzzle game built in Godot 4.4 with GDScript.", [r"godot"]),
        G("g2", "Pebble targets a locked 60 fps on the Pixel 8 (min-spec device).", [r"60 ?fps|60 frames|\b60\b", r"pixel ?8"]),
        G("g3", "Decision: physics on a fixed 120 Hz tick with render interpolation.", [r"120 ?hz|120 ?(physics )?ticks?|120 per"], type="episodic"),
    ],
    opt=[OPT("User fixed Pebble frame-pacing jank.", [r"jank|frame pacing|stutter|frame time", r"fix|resolv|gone|lock|solv"])],
    traps=[TRAP("t1", "Pebble uses Unity.", [r"\bunity\b"], unless=[NEG, r"godot"], why="assistant counterfactual")],
))

# ---------------------------------------------------------------- A03
RUST_ERR = """error[E0277]: `Rc<RefCell<Manifest>>` cannot be sent between threads safely
   --> src/sync/upload.rs:88:24
    |
88  |         tokio::spawn(async move {
    |         ------------ ^^^^^^^^^^ `Rc<RefCell<Manifest>>` cannot be sent between threads safely
    |
    = help: within `{async block@src/sync/upload.rs:88:24}`, the trait `Send` is not implemented
note: required by a bound in `tokio::spawn`
"""
CONVS.append(conv(
    "A03_rust_cli", ["coding", "preference", "instruction"], "ferrite-sync Send error",
    [session("2026-09-16T18:30:00Z", [
        ("Building ferrite-sync, a Rust CLI that mirrors local folders to object storage. Using clap for args and tokio. Getting this:\n\n```\n" + RUST_ERR + "```",
         "You're moving an `Rc<RefCell<_>>` into a spawned task. Swap it for `Arc<Mutex<_>>` (tokio's Mutex if you hold it across awaits). Alternatively async-std's spawn_local would avoid Send, but that's a runtime switch."),
        ("please don't suggest async-std again, we standardized on tokio across all our Rust projects",
         "Understood: tokio everywhere. Use `Arc<tokio::sync::Mutex<Manifest>>`."),
        ("Is this idiomatic?\n\n```rust\n" + CODE(301, 5, "rs") + "\n```",
         "Mostly. For the error type: in the library crate use `thiserror` to define typed errors; in the binary use `anyhow` with `.context()`."),
        ("Yeah that's my rule: anyhow in binaries, thiserror in library crates. Keep that in mind for future reviews.",
         "Will do: anyhow for binaries, thiserror for libraries."),
        ("cool, shipping ferrite-sync v0.3.0 tonight", "Nice. Tag it and double-check the release build with `cargo build --release --locked`."),
    ])],
    gold=[
        G("g1", "ferrite-sync is a Rust CLI mirroring folders to object storage, using clap and tokio.", [r"ferrite-?sync|rust cli", r"clap|object storage|mirror"]),
        G("g2", "The user standardized on tokio for all Rust projects (no async-std).", [r"tokio", r"async-?std|standardi|all (our|of|rust)|across|everywhere"], types=["persona", "instruction"]),
        G("g3", "Rule: anyhow in binaries, thiserror in library crates.", [r"anyhow", r"thiserror"], type="instruction"),
        G("g4", "User planned to ship ferrite-sync v0.3.0 on 2026-09-16.", [r"0\.3\.0|v0\.3"], type="episodic"),
    ],
    traps=[TRAP("t1", "User uses async-std.", [r"async-?std"], unless=[NEG, r"tokio"], why="rejected suggestion")],
))

# ---------------------------------------------------------------- A04
CONVS.append(conv(
    "A04_python_etl", ["coding", "preference"], "ETL with uv and DuckDB",
    [session("2026-09-17T14:00:00Z", [
        ("Need help with an ETL script that pulls CSV exports and aggregates them per bucket. Sample:\n\n" + CSV(401, 30),
         "Here's a starting point:\n\n```bash\npip install pandas\n```\n\n```python\n" + CODE(402, 3, "py") + "\n```"),
        ("no pip please. I use uv for everything Python (`uv add`, `uv run`). And target Python 3.12.",
         "Understood: `uv add pandas` and `uv run etl.py`, targeting Python 3.12."),
        ("This blows up on the bigger exports:\n\n" + TRACE(403, 12, "py"),
         "The full frame doesn't fit in memory. Two options: chunked reads with pandas, or push aggregation into DuckDB which streams the CSV directly."),
        ("Let's go with DuckDB for the local analytics layer; pandas only for the final small frames.",
         "Done:\n\n```python\n" + CODE(404, 3, "py") + "\n```"),
        ("works. 40s -> 3s on the big export", "Nice speedup."),
    ])],
    gold=[
        G("g1", "The user uses uv instead of pip for Python.", [r"\buv\b"], types=["persona", "instruction"]),
        G("g2", "The user targets Python 3.12.", [r"3\.12"], types=["persona", "episodic"]),
        G("g3", "Decision: DuckDB for the local analytics layer, pandas only for small final frames.", [r"duckdb"], type="episodic"),
    ],
    opt=[OPT("ETL went from 40s to 3s.", [r"40 ?s|3 ?s|seconds|speed"])],
    traps=[TRAP("t1", "User uses pip.", [r"\bpip\b"], unless=[NEG, r"\buv\b"], why="assistant default; user corrected")],
))

# ---------------------------------------------------------------- A05 (long logs)
CONVS.append(conv(
    "A05_flaky_ci", ["coding", "instruction", "long"], "Flaky CI race",
    [session("2026-09-18T16:00:00Z", [
        ("Our GitHub Actions CI for Lantern has been flaky for a week; about 1 in 5 runs fail on the integration job. Failing run log:\n\n" + LOG(501, 140),
         "The failure happens right after the `drain` event in the teardown phase. Can you share a passing run for comparison?"),
        ("Passing run for comparison:\n\n" + LOG(502, 140),
         "In the passing run the final flush completes before teardown. In the failing run teardown closes the temp server first. Looks like a race in fixture teardown."),
        ("You're right: the teardown closes the temp server before the last flush finishes. Fixed it by awaiting the drain promise:\n\n" + DIFF(503, 14, "ts"),
         "That's the correct fix: await the drain before closing the server."),
        ("Policy going forward: CI retries a failing job at most 2 times, and any test that needed a retry gets a ticket. Remember that when you touch CI configs.",
         "Noted: max 2 retries per failing job, and every retried test gets a ticket."),
        ("20 green runs in a row. closing this out.", "Great, the race is fixed."),
    ])],
    gold=[
        G("g1", "Fixed flaky Lantern CI: race in fixture teardown (server closed before final flush), fixed by awaiting drain.", [r"flak|race|teardown", r"fix|resolv|await|drain"], type="episodic"),
        G("g2", "CI policy: retry a failing job at most 2 times; retried tests get a ticket.", [r"retr", r"\b2\b|two|twice"], type="instruction"),
    ],
    opt=[OPT("Lantern CI runs on GitHub Actions.", [r"github actions"]),
         OPT("CI was failing 1 in 5 runs.", [r"1 in 5|20 ?%|one in five"])],
))

# ---------------------------------------------------------------- A06
CONVS.append(conv(
    "A06_k8s_deploy", ["coding", "instruction", "preference"], "Atlas on homelab k3s",
    [session("2026-09-19T20:00:00Z", [
        ("Deploying Atlas (our memory service) to my homelab k3s cluster. The only GPU node is 'forge', the 3080 Ti box. Pods stuck Pending:\n\n" + LOG(601, 30),
         "The pods request a GPU but have no node selector or toleration for forge. Add `nodeSelector: kubernetes.io/hostname: forge` and the matching toleration."),
        ("Everything is reachable over Tailscale only; nothing gets exposed publicly. Keep that constraint in anything you suggest.",
         "Understood: Tailscale-only access, no public ingress."),
        ("Here's the values file:\n\n" + JSONCFG(602, 18),
         "Change the service type from LoadBalancer to ClusterIP and expose it through the Tailscale operator instead."),
        ("Important: never run kubectl delete or helm uninstall on my cluster without asking me first. Ever.",
         "Understood. I will always ask before any kubectl delete or helm uninstall."),
        ("Atlas is up on forge. latency looks fine.", "Good. p50 under 5 ms on search is what we expected."),
        ("btw I'm thinking about maybe moving the cluster to Talos someday, no concrete plans though",
         "Talos is nice for immutable nodes; worth a look when you have time."),
    ])],
    gold=[
        G("g1", "Homelab k3s cluster; the only GPU node is 'forge' (RTX 3080 Ti).", [r"forge", r"3080|gpu"]),
        G("g2", "Services are reachable only via Tailscale; nothing exposed publicly.", [r"tailscale"], types=["persona", "instruction"]),
        G("g3", "Never run kubectl delete / helm uninstall without asking first.", [r"kubectl delete|helm uninstall|delet\w*|uninstall", r"ask|permission|confirm|approv|first"], type="instruction"),
        G("g4", "Deployed Atlas to the homelab k3s cluster on forge.", [r"atlas", r"deploy|k3s|cluster|running|\bup\b"], type="episodic"),
    ],
    opt=[OPT("Uses k3s at home.", [r"k3s|homelab"])],
    traps=[TRAP("t1", "User moved / plans to move to Talos.", [r"talos"], unless=[r"maybe|someday|consider|thinking|might|no (concrete )?plans?|possib|eventual|explor"], why="tentative musing")],
    l2=[L2F("tailscale", [r"tailscale"]), L2F("forge", [r"forge"])],
    l3=[L2F("tailscale", [r"tailscale"])],
))

# ---------------------------------------------------------------- A07
CONVS.append(conv(
    "A07_style_rules", ["coding", "instruction"], "TS style rules",
    [session("2026-09-20T13:00:00Z", [
        ("Can you refactor this module? Style rules for all my TS code: 2-space indent, named exports only, no default exports.\n\n```ts\n" + CODE(701, 6, "ts") + "\nexport default runAll;\n```",
         "Refactored with named exports and 2-space indentation:\n\n```ts\n" + CODE(702, 4, "ts") + "\n```"),
        ("also commit messages: Conventional Commits format, always.",
         "Will do. Example: `refactor(scheduler): convert default export to named exports`."),
        ("review this?\n\n" + DIFF(703, 20, "ts"), "Looks fine; one nit: the second hunk reintroduces a default export."),
        ("good catch. lgtm otherwise, thanks", "You're welcome."),
    ])],
    gold=[
        G("g1", "TS style: 2-space indentation.", [r"2[- ]space|two[- ]space"], type="instruction"),
        G("g2", "TS style: named exports only, no default exports.", [r"default export|named export"], type="instruction"),
        G("g3", "Commit messages must use Conventional Commits.", [r"conventional commit"], type="instruction"),
    ],
))

# ---------------------------------------------------------------- A08 (heavy window)
CONVS.append(conv(
    "A08_heavy_window", ["coding", "long", "heavy"], "ai SDK upgrade breakage (heavy window)",
    [session("2026-09-21T09:00:00Z", [
        ("Morning. Lantern's build broke after upgrading the ai SDK. Full output below, sorry it's long:\n\n"
         + TRACE(801, 60, "ts") + "\n\n" + LOG(802, 260) + "\n\n```ts\n" + CODE(803, 14, "ts") + "\n```",
         "The root error is the streaming helper's changed return type; most of the log is cascade. The relevant frames are the first eight. Fix:\n\n```ts\n" + CODE(804, 6, "ts") + "\n```\n\n" + PROSE(805, 12)),
        ("Still failing after that patch:\n\n" + LOG(806, 220) + "\n\n" + DIFF(807, 60, "ts"),
         "This is a regression in the new minor version's streaming path, not your code. " + PROSE(808, 10)),
        ("OK decision: pin the `ai` package to exactly 6.0.164 in Lantern until the streaming regression is fixed upstream.",
         "Pinned: `\"ai\": \"6.0.164\"` with an exact version, plus a comment linking the upstream issue."),
        ("rebuilding... works. bye", "Great."),
    ])],
    gold=[G("g1", "Decision: pin the ai package to exactly 6.0.164 in Lantern until the upstream streaming regression is fixed.", [r"6\.0\.164"], type="episodic")],
    opt=[OPT("Lantern build broke after upgrading the ai SDK.", [r"\bai\b|sdk", r"upgrad|broke|break|regress|fail"])],
))

# ---------------------------------------------------------------- B01
CONVS.append(conv(
    "B01_db_choice", ["decision", "preference"], "Atlas datastore decision",
    [session("2026-09-03T17:00:00Z", [
        ("Design question for Atlas, our memory service: Postgres + pgvector, or SQLite with FTS5? It runs on a single host and I care about ops simplicity.",
         "For single-host with modest data, SQLite + FTS5 wins on ops. Postgres + pgvector gives you vectors and concurrency. A third option is Qdrant as a dedicated vector store next to SQLite."),
        ("Not adding Qdrant. Another service to babysit is exactly what I don't want.", "Fair: Qdrant is out."),
        ("Decision: SQLite + FTS5 for Atlas, no embeddings in v1. We revisit vectors only if BM25 recall is clearly insufficient.",
         "Recorded: SQLite + FTS5 (BM25), no embeddings in v1; revisit vectors only if recall is insufficient."),
        ("Also, I care a lot about being able to back up the whole thing with a single file copy.",
         "SQLite gives you that; use the online backup API or `VACUUM INTO` for a consistent copy."),
        ("thanks, writing up the ADR now", "Sounds good."),
    ])],
    gold=[
        G("g1", "Decision: Atlas uses SQLite + FTS5 (BM25), no embeddings in v1.", [r"sqlite|fts5"], type="episodic"),
        G("g2", "Vectors will be revisited only if BM25 recall is clearly insufficient.", [r"bm25|recall|vector|embedding", r"revisit|only if|later|insufficient|\bv1\b|no embedding"], type="episodic"),
        G("g3", "The user values ops simplicity and single-file backups.", [r"single[- ]file|backup|simplicit|babysit|fewer services|operational|ops"]),
    ],
    traps=[TRAP("t1", "Atlas uses Qdrant.", [r"qdrant"], unless=[NEG], why="rejected suggestion"),
           TRAP("t2", "Atlas uses Postgres/pgvector.", [r"postgres|pgvector"], unless=[NEG, r"sqlite|fts5"], why="rejected option")],
    l2=[L2F("sqlite fts5", [r"sqlite|fts5"])],
    l3=[L2F("ops simplicity", [r"simplicit|single[- ]file|backup|ops|operational|运维|简单|单文件|备份|简洁"])],
))

# ---------------------------------------------------------------- B02
CONVS.append(conv(
    "B02_vendor_llm", ["decision", "preference"], "Models for background jobs",
    [session("2026-09-04T19:00:00Z", [
        ("For background jobs (summaries, tagging) I want cheap models. Leaning toward OpenRouter's free tier.",
         "Free tiers work for low-stakes background jobs. Honestly for quality I'd just use GPT-5 for everything and stop worrying."),
        ("No, quality isn't worth paying for on background jobs. Free models for background, paid only for interactive work.",
         "Understood: free models for background jobs, paid models only for interactive use."),
        ("And the experiments budget is capped at $5 a month, hard cap.", "Noted: $5/month hard cap for experiments."),
        ("Write me a quick checklist for evaluating a free model.",
         "Checklist:\n- schema adherence on your real prompts\n- latency p95\n- rate limits and daily caps\n- data retention / training policy\n- fallback behaviour on 429s\n" + PROSE(201, 6)),
        ("good", "👍"),
    ])],
    gold=[
        G("g1", "Free (OpenRouter) models for background jobs; paid models only for interactive work.", [r"free", r"background"], types=["persona", "episodic", "instruction"]),
        G("g2", "Experiments budget is hard-capped at $5/month.", [r"\$ ?5\b|5 dollars|five dollars|\$5/"], types=["persona", "episodic", "instruction"]),
    ],
    traps=[TRAP("t1", "User uses GPT-5 for everything.", [r"gpt-?5"], unless=[NEG], why="rejected suggestion")],
))

# ---------------------------------------------------------------- B03
CONVS.append(conv(
    "B03_launch_plan", ["decision", "plan"], "Pebble soft launch plan",
    [session("2026-09-05T15:30:00Z", [
        ("Planning Pebble's soft launch. We're doing Canada and New Zealand only, on 2026-11-15.",
         "Good test markets. Plan store listings and a crash-reporting pass two weeks ahead."),
        ("No paid UA during soft launch, organic only. Goal is D1 retention above 40% before we go global.",
         "Clear: organic only, and D1 > 40% is the gate for global."),
        ("Can you draft a launch checklist?",
         "Soft-launch checklist:\n1. Store listings for CA/NZ\n2. Analytics events verified\n3. Crash reporting\n4. Retention dashboard (D1/D7)\n5. Support inbox\n" + PROSE(301, 8)),
        ("great", "Good luck with the launch."),
    ])],
    gold=[
        G("g1", "Pebble soft launch in Canada and New Zealand on 2026-11-15.", [r"canada|new zealand|\bnz\b", r"11-15|november 15|nov\.? 15|15 nov"], type="episodic"),
        G("g2", "No paid user acquisition during soft launch (organic only).", [r"organic|paid (ua|user acquisition|acquisition|ads)|no paid"], types=["episodic", "persona"]),
        G("g3", "Goal: D1 retention above 40% before global launch.", [r"40 ?%|40 percent", r"retention|\bd1\b"], type="episodic"),
    ],
    l2=[L2F("soft launch markets", [r"canada|new zealand|\bnz\b|加拿大|新西兰"])],
))

# ---------------------------------------------------------------- B04
CONVS.append(conv(
    "B04_hiring", ["decision", "third_party"], "Pebble art: contractor vs full-time",
    [session("2026-09-06T21:00:00Z", [
        ("Need to decide how to get art done for Pebble. My friend Dana says contractors are always risky and I should hire full-time.",
         "Full-time gives continuity; a contractor is cheaper and lower commitment at your stage."),
        ("I'm going with a contractor for the art: 3-month engagement, budget $4k. Full-time doesn't make sense at our stage.",
         "Makes sense. A fixed scope with milestones keeps the risk down."),
        ("Draft a short brief for the contractor posting.",
         "Brief: 2D puzzle-game art, 3-month engagement, ~40 tiles, 6 UI screens, 1 app icon; budget $4,000; weekly check-ins.\n" + PROSE(401, 5)),
        ("thx", "Anytime."),
    ])],
    gold=[
        G("g1", "Decided to hire a contractor (3-month engagement) for Pebble art rather than full-time.", [r"contract", r"art"], type="episodic"),
        G("g2", "Pebble art contractor budget is $4k.", [r"\$ ?4 ?k|\$ ?4,?000|\b4000\b|4 ?k"], type="episodic"),
    ],
    traps=[TRAP("t1", "User plans to hire full-time.", [r"full[- ]time"], unless=[NEG, r"dana|friend"], why="friend's advice, rejected"),
           TRAP("t2", "User thinks contractors are risky.", [r"risky"], unless=[r"dana|friend"], why="third-party opinion")],
))

# ---------------------------------------------------------------- B05
CONVS.append(conv(
    "B05_queue", ["decision", "instruction"], "Atlas job queue without Redis",
    [session("2026-09-07T16:00:00Z", [
        ("Atlas needs a job queue for extraction tasks. Last time you suggested Redis + BullMQ.",
         "Redis + BullMQ is battle-tested: retries, delays, rate limits out of the box."),
        ("I'm dropping Redis from the plan. The queue lives in SQLite: a jobs table with leases. One less daemon.",
         "Fine at your scale. Use `UPDATE ... RETURNING` to claim a job atomically with a lease expiry."),
        ("Does this lease logic look right?\n\n```ts\n" + CODE(501, 4, "ts") + "\n```",
         "The lease renewal must check the owner id; otherwise two workers can both renew."),
        ("Max 3 attempts per job, then it goes to a dead-letter table. That's the rule.",
         "Noted: 3 attempts, then dead-letter."),
        ("cool", "Done."),
    ])],
    gold=[
        G("g1", "Atlas job queue lives in SQLite (jobs table with leases); no Redis.", [r"sqlite|jobs? table|lease"], type="episodic"),
        G("g2", "Jobs get max 3 attempts, then go to a dead-letter table.", [r"\b3\b|three", r"attempt|retr|dead[- ]letter"], types=["episodic", "instruction"]),
    ],
    traps=[TRAP("t1", "Atlas uses Redis/BullMQ.", [r"redis|bullmq"], unless=[NEG], why="rejected suggestion")],
))

# ---------------------------------------------------------------- B06
CONVS.append(conv(
    "B06_license", ["decision"], "Lantern license",
    [session("2026-09-08T18:00:00Z", [
        ("Open-sourcing Lantern next month. Considering AGPL vs MIT.",
         "AGPL protects against closed SaaS forks; MIT maximizes adoption."),
        ("AGPL would scare off the people I want using it. Going MIT.", "MIT it is."),
        ("Do I need a NOTICE file? Also what's the SPDX id?",
         "No NOTICE needed for MIT; the SPDX identifier is `MIT`. Put `SPDX-License-Identifier: MIT` in file headers if you like."),
        ("ok", "👍"),
    ])],
    gold=[G("g1", "Lantern will be open-sourced under MIT.", [r"\bmit\b"], type="episodic")],
    opt=[OPT("Lantern open-sourcing planned next month.", [r"open[- ]sourc"])],
    traps=[TRAP("t1", "Lantern uses AGPL.", [r"agpl"], unless=[NEG, r"\bmit\b"], why="rejected option")],
))

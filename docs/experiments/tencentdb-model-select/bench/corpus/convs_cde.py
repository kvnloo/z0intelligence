"""C = preferences, D = corrections/supersessions, E = noise / nothing-to-extract. Synthetic."""
from dsl import (CODE, CSV, DIFF, G, JSONCFG, L2F, LOG, NEG, OLD, OPT, PROSE, SUP, TRACE, TRAP, DUP,
                 conv, session)

CONVS = []

# ---------------------------------------------------------------- C01
CONVS.append(conv(
    "C01_comms_style", ["preference", "instruction"], "Answer style",
    [session("2026-09-09T08:00:00Z", [
        ("I'm Rowan, by the way. Quick ask: keep answers terse. No emojis, no 'Great question!' preambles. Bullets are fine.",
         "Got it, Rowan. Terse, no emojis, no preambles."),
        ("What's the difference between a mutex and a semaphore?",
         "- Mutex: one holder, owner must release.\n- Semaphore: N permits, any thread can release.\n- Use a mutex for exclusive access, a semaphore to bound concurrency."),
        ("Perfect, that's the length I want. And when you're unsure about something, say so explicitly instead of guessing.",
         "Will do. I'll flag uncertainty explicitly."),
        ("explain CAP theorem in 3 bullets",
         "- Consistency: every read sees the latest write.\n- Availability: every request gets a response.\n- Under a partition you must give up one of the two."),
        ("good", "OK."),
    ])],
    gold=[
        G("g1", "The user wants terse answers without preambles; bullets are fine.", [r"terse|concise|short|brief|succinct"], type="instruction"),
        G("g2", "No emojis in answers.", [r"emoji"], type="instruction"),
        G("g3", "When unsure, say so explicitly instead of guessing.", [r"unsure|uncertain|not sure|guess|uncertainty"], type="instruction"),
    ],
    opt=[OPT("User's name is Rowan.", [r"rowan"])],
    l2=[L2F("terse", [r"terse|concise|short|brief|succinct|简洁|简短|简明|精炼|简练"])],
    l3=[L2F("terse", [r"terse|concise|short|brief|succinct|简洁|简短|简明|精炼|简练"]), L2F("no emojis", [r"emoji|表情"])],
))

# ---------------------------------------------------------------- C02
CONVS.append(conv(
    "C02_food_health", ["preference", "health", "third_party"], "Meal prep with allergy",
    [session("2026-09-10T17:30:00Z", [
        ("Meal-prep help: I'm vegetarian, and seriously allergic to peanuts (anaphylaxis level), so never suggest anything with peanuts.",
         "Understood: vegetarian, strictly no peanuts or peanut-derived ingredients."),
        ("My partner Sam loves peanut butter though, so we keep it in the house, just separate.",
         "Makes sense; separate shelf and utensils."),
        ("Give me 5 high-protein lunches.",
         "1. Lentil and quinoa bowl\n2. Tofu stir-fry with edamame\n3. Chickpea salad wraps\n4. Tempeh and black-bean burrito bowl\n5. Greek yogurt with seeds (no peanut products)"),
        ("Thanks. I usually cook on Sundays for the whole week.", "Then batch the grains and proteins on Sunday and assemble daily."),
    ])],
    gold=[
        G("g1", "The user has a severe (anaphylactic) peanut allergy.", [r"peanut", r"allerg|anaphyla"]),
        G("g2", "The user is vegetarian.", [r"vegetarian"]),
        G("g3", "The user meal-preps on Sundays for the week.", [r"sunday"]),
    ],
    opt=[OPT("Partner Sam loves peanut butter.", [r"sam|partner", r"peanut"])],
    traps=[TRAP("t1", "User loves peanut butter.", [r"love|like|enjoy|fond", r"peanut"], unless=[r"\bsam\b|partner"], why="third-party preference")],
    l2=[L2F("peanut allergy", [r"peanut|花生"])],
    l3=[L2F("peanut allergy", [r"peanut|花生"]), L2F("vegetarian", [r"vegetarian|素食|吃素"])],
))

# ---------------------------------------------------------------- C03
CONVS.append(conv(
    "C03_schedule", ["preference"], "Weekly planning",
    [session("2026-09-11T05:45:00Z", [
        ("Help me plan my week. I do deep work best between 6 and 10am; my brain is mush after 3pm.",
         "Then schedule the hard tasks 6-10am and admin after 3pm."),
        ("No meetings on Fridays. That's a hard rule I've kept for two years.", "Got it, Fridays stay meeting-free."),
        ("Task list:\n- finish Atlas search API\n- write investor update\n- review contractor portfolio\n- fix Pebble crash on resume\n- tax paperwork\n- plan soft-launch analytics",
         "Mon-Thu 6-10am: Atlas search API, Pebble crash, analytics plan, investor update. Afternoons: portfolio review, paperwork. Friday: deep work only."),
        ("ok", "Have a good week."),
    ])],
    gold=[
        G("g1", "The user does deep work best between 6 and 10am.", [r"\b6\b.{0,15}\b10 ?(am)?\b|\b6 ?am\b|morning"]),
        G("g2", "No meetings on Fridays (hard rule).", [r"friday", r"meeting"], types=["persona", "instruction"]),
    ],
    opt=[OPT("Low energy after 3pm.", [r"3 ?pm|afternoon"])],
))

# ---------------------------------------------------------------- C04
CONVS.append(conv(
    "C04_editor_tools", ["preference"], "New laptop setup",
    [session("2026-09-12T22:00:00Z", [
        ("Setting up a new laptop. My stack: Neovim, tmux, zsh. I prefer CLI tools to GUIs whenever possible.",
         "Then: a plugin manager for Neovim, tpm for tmux, and a minimal zsh prompt."),
        ("Dark themes only. I get headaches from light mode.", "Dark themes everywhere then."),
        ("Give me a tmux.conf starter.",
         "```\nset -g mouse on\nset -g base-index 1\nset -g history-limit 50000\nbind r source-file ~/.tmux.conf\nset -g status-style bg=colour235,fg=colour250\n```"),
        ("nice", "Enjoy the new machine."),
    ])],
    gold=[
        G("g1", "The user uses Neovim (with tmux and zsh).", [r"neovim|nvim"]),
        G("g2", "The user prefers CLI tools over GUIs.", [r"\bcli\b|command[- ]line|terminal", r"\bgui|graphical"]),
        G("g3", "Dark themes only; light mode gives the user headaches.", [r"dark"]),
    ],
    opt=[OPT("Uses tmux.", [r"tmux"]), OPT("Uses zsh.", [r"zsh"])],
    l3=[L2F("neovim", [r"neovim|nvim"]), L2F("dark", [r"dark|深色|暗色|暗黑|黑色主题"])],
))

# ---------------------------------------------------------------- C05
CONVS.append(conv(
    "C05_japanese", ["preference", "instruction"], "Japanese study",
    [session("2026-09-13T11:00:00Z", [
        ("I'm learning Japanese, roughly JLPT N4 level. I study 30 minutes a day with Anki.",
         "Good routine. At N4, add graded readers for context."),
        ("From now on, when I type 'jp drill', quiz me with 5 N4 vocab words and grade my answers.", "Deal."),
        ("jp drill", "1. 天気  2. 駅  3. 宿題  4. 忙しい  5. 約束"),
        ("1. tenki, weather 2. eki, station 3. shukudai, homework 4. isogashii, busy 5. yakusoku, appointment?",
         "5/5. Note 約束 is more 'promise' than appointment, but accepted."),
        ("thanks", "お疲れ様でした。"),
    ])],
    gold=[
        G("g1", "The user is learning Japanese at about JLPT N4.", [r"japanese", r"\bn4\b|jlpt"]),
        G("g2", "The user studies 30 minutes a day with Anki.", [r"anki|30 ?min"]),
        G("g3", "When the user types 'jp drill', quiz 5 N4 vocab words and grade.", [r"jp drill|drill|quiz"], type="instruction"),
    ],
))

# ---------------------------------------------------------------- C06
CONVS.append(conv(
    "C06_units", ["preference", "instruction"], "Units and formats",
    [session("2026-09-14T07:00:00Z", [
        ("Convert this recipe for me: 2 cups flour, 1 stick butter, bake at 350F for 25 minutes in a 9x13 inch pan.",
         "250 g flour, 113 g butter, bake at 175 °C for 25 minutes in a 23x33 cm pan."),
        ("Always use metric units and 24-hour time with me. And ISO dates (YYYY-MM-DD).", "Understood: metric, 24-hour, ISO 8601 dates."),
        ("when is 3:30pm Pacific in Berlin tomorrow?", "00:30 the following day in Berlin (CEST, UTC+2)."),
        ("ok", "OK."),
    ])],
    gold=[
        G("g1", "Always use metric units.", [r"metric"], type="instruction"),
        G("g2", "Always use 24-hour time.", [r"24[- ]?h|24-hour|24 hour"], type="instruction"),
        G("g3", "Use ISO dates (YYYY-MM-DD).", [r"\biso\b|yyyy-mm-dd"], type="instruction"),
    ],
))

# ---------------------------------------------------------------- D01 within-session supersession
CONVS.append(conv(
    "D01_host_move", ["correction", "supersession"], "Atlas hosting correction",
    [session("2026-09-15T13:00:00Z", [
        ("We host Atlas on Fly.io right now, two regions.", "OK. Fly makes multi-region easy."),
        ("Health checks keep flapping:\n\n" + LOG(1101, 40), "The check hits /health before migrations finish. Add a grace period."),
        ("Config:\n\n" + JSONCFG(1102, 14), "Raise `watcher_timeout_ms`; it's below the migration time."),
        ("better. now the search endpoint is slow on cold start", "Warm the FTS index at boot with a dummy query."),
        ("Here's the boot sequence\n\n```ts\n" + CODE(1103, 3, "ts") + "\n```", "Move the warm-up after the migration step."),
        ("ok that works", "Good."),
        ("Correction to what I said earlier: we actually moved Atlas off Fly.io to a Hetzner box last month. Fly is gone.",
         "Thanks, that changes the deploy path: no Fly config, a single Hetzner host."),
        ("so update the deploy script for Hetzner", "```bash\nrsync -az dist/ atlas@hetzner-1:/srv/atlas/\nssh atlas@hetzner-1 'systemctl --user restart atlas'\n```"),
        ("thanks", "You're welcome."),
    ])],
    gold=[G("g1", "Atlas is hosted on a Hetzner box (moved off Fly.io last month).", [r"hetzner"], type="episodic")],
    sups=[SUP("s1", "g1", [r"\bfly(\.io)?\b"], unless=[r"hetzner", OLD, r"off fly|gone"], scope="within")],
))

# ---------------------------------------------------------------- D02 name correction
CONVS.append(conv(
    "D02_name_fix", ["correction", "supersession", "third_party"], "Cofounder name correction",
    [session("2026-09-16T10:00:00Z", [
        ("My cofounder Jon handles all the business side; I do the engineering.", "Good split."),
        ("We need an investor update. Last month: 1.2k WAU on the Lantern beta, 3 design partners.", "Draft:\n" + PROSE(1201, 6)),
        ("Make it shorter and add the hiring note (art contractor).", "Shorter draft:\n" + PROSE(1202, 4)),
        ("better", "Good."),
        ("Oops, typo earlier: my cofounder's name is Jonah, not Jon.", "Thanks, I'll use Jonah."),
        ("sign it from both of us", "Signed: Rowan & Jonah."),
    ])],
    gold=[
        G("g1", "The user's cofounder is Jonah, who handles the business side.", [r"jonah"]),
        G("g2", "The user does the engineering.", [r"engineer|technical side"]),
    ],
    opt=[OPT("Lantern beta 1.2k WAU, 3 design partners.", [r"1\.2 ?k|wau|design partner"])],
    sups=[SUP("s1", "g1", [r"\bjon\b"], unless=[r"jonah", OLD, r"typo|correct|misspel"], scope="within")],
))

# ---------------------------------------------------------------- D03 preference change
CONVS.append(conv(
    "D03_pref_change", ["correction", "supersession", "instruction"], "Answer length change",
    [session("2026-09-17T09:00:00Z", [
        ("I like detailed, thorough explanations with background. Don't skimp. Explain how B-trees work.",
         "B-trees are balanced search trees optimized for block storage. " + PROSE(1301, 14)),
        ("And LSM trees?", "LSM trees buffer writes in memory and flush sorted runs. " + PROSE(1302, 14)),
        ("Compare their write amplification.", PROSE(1303, 16)),
        ("What about read amplification?", PROSE(1304, 16)),
        ("ok", "Anything else?"),
        ("Actually, change of plan: from now on keep it short. The detailed answers are slowing me down.", "Understood: short answers from now on."),
        ("Which one for a write-heavy log store?", "LSM tree."),
    ])],
    gold=[G("g1", "From now on the user wants short answers (changed from detailed).", [r"short|concise|brief|terse|succinct"], type="instruction")],
    sups=[SUP("s1", "g1", [r"detail|thorough|in-depth|in depth"], unless=[r"short|concise|brief|terse|succinct", OLD], scope="within")],
    l3=[L2F("short answers", [r"short|concise|brief|terse|succinct|简短|简洁|简明|精炼|简练"])],
))

# ---------------------------------------------------------------- D04 date change
CONVS.append(conv(
    "D04_date_change", ["correction", "supersession", "plan"], "Global launch date slip",
    [session("2026-09-18T15:00:00Z", [
        ("Pebble global launch is set for November 15.", "Then marketing assets are due by end of October."),
        ("List the store assets we need.", "- 6.7\" and 5.5\" screenshots\n- feature graphic\n- 30 s preview video\n- localized descriptions"),
        ("Draft the short description.", "Pebble: slide, stack, and solve 300 handcrafted puzzles."),
        ("ok", "OK."),
        ("Update: global launch slipped to December 1 because of the store review backlog.", "Noted: global launch on December 1."),
        ("shift the asset deadline accordingly", "Assets now due mid-November."),
    ])],
    gold=[G("g1", "Pebble global launch is December 1 (slipped from Nov 15 due to store review backlog).", [r"dec(ember)?\.? ?1\b|12-01|\b1 dec"], type="episodic")],
    sups=[SUP("s1", "g1", [r"nov(ember)?\.? ?15|11-15|15 nov"], unless=[r"dec(ember)?|12-01", OLD, r"slip|delay|moved|postpon|push"], scope="within")],
))

# ---------------------------------------------------------------- D05 number correction
CONVS.append(conv(
    "D05_budget_fix", ["correction", "supersession"], "Marketing budget correction",
    [session("2026-09-19T12:00:00Z", [
        ("Marketing budget for the Pebble launch is $2k.", "With $2k, focus on creator outreach."),
        ("Which channels?", "Short-form video creators in the puzzle niche, plus Reddit community posts."),
        ("ok what else", "A small press kit page."),
        ("I misspoke earlier: it's $20k, not $2k. Big difference for the plan.", "That changes things: you can add paid creator deals and a small ad test."),
        ("redo the allocation", "$20k: 50% creators, 25% ad test, 15% assets, 10% reserve."),
    ])],
    gold=[G("g1", "Pebble launch marketing budget is $20k.", [r"\$ ?20 ?k|\b20,000\b|\b20000\b|20k"], type="episodic")],
    sups=[SUP("s1", "g1", [r"\$ ?2 ?k\b|\b2,?000\b"], unless=[r"20 ?k|20,000|20000", OLD, r"misspo|correct"], scope="within")],
))

# ---------------------------------------------------------------- D06 retraction
NODE_STAY = r"stay|remain|keep|stick|not (switch|mov)|abandon|cancel|never mind|decided against|won'?t|wash|break"
CONVS.append(conv(
    "D06_bun_retraction", ["correction", "supersession"], "Bun switch retracted",
    [session("2026-09-20T16:00:00Z", [
        ("I'm switching Lantern's runtime from Node to Bun next week.", "Bun is fast to start; watch native addons."),
        ("Run me through the risks.", "Native modules, some node: APIs, test runner differences. " + PROSE(1601, 6)),
        ("benchmarks:\n\n" + CSV(1602, 20), "Roughly equal throughput; Bun starts faster."),
        ("Never mind the Bun thing. Benchmarks were a wash and two deps break. Staying on Node.", "Makes sense; staying on Node."),
        ("ok", "OK."),
    ])],
    gold=[G("g1", "Lantern stays on Node (Bun switch abandoned).", [r"\bnode\b", NODE_STAY], type="episodic")],
    sups=[SUP("s1", "g1", [r"\bbun\b"], unless=[NODE_STAY, OLD], scope="within")],
))

# ---------------------------------------------------------------- E01..E06 noise
CONVS.append(conv(
    "E01_trivia", ["noise"], "Trivia lookups",
    [session("2026-09-21T19:00:00Z", [
        ("what's the capital of Australia", "Canberra."),
        ("5 miles in km?", "About 8.05 km."),
        ("boiling point of water at 2000 m altitude?", "About 93 °C."),
        ("thx", "You're welcome."),
    ])],
))
CONVS.append(conv(
    "E02_translation", ["noise"], "One-off translation",
    [session("2026-09-22T10:00:00Z", [
        ("Translate this paragraph to Spanish:\n\n" + PROSE(1801, 6), "El planificador ... (traducción completa)."),
        ("and to French", "Le planificateur ... (traduction complète)."),
        ("thanks", "De nada."),
    ])],
))
CONVS.append(conv(
    "E03_regex", ["noise", "coding"], "Email regex one-off",
    [session("2026-09-22T14:00:00Z", [
        ("Write a regex that validates emails.", "```\n^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$\n```\nGood enough for UI validation; real validation is sending a confirmation mail."),
        ("now in Python with tests", "```python\n" + CODE(1901, 3, "py") + "\n```"),
        ("thanks", "You're welcome."),
    ])],
))
CONVS.append(conv(
    "E04_smalltalk", ["noise"], "Small talk",
    [session("2026-09-23T08:00:00Z", [
        ("morning!", "Good morning!"),
        ("how's it going", "All good here. What are we working on?"),
        ("nothing yet, just coffee. weather's nice today", "Enjoy it."),
        ("ok later", "See you."),
    ])],
))
CONVS.append(conv(
    "E05_error_meaning", ["noise", "long"], "What does this error mean",
    [session("2026-09-23T13:00:00Z", [
        ("what does this error mean?\n\n" + LOG(2001, 60) + "\n\n" + TRACE(2002, 16, "py"),
         "The worker lost its lease while waiting on a slow call, so the commit was rejected (`lock not held`). Usually a lease shorter than the slowest operation."),
        ("ok thanks", "You're welcome."),
    ])],
))
CONVS.append(conv(
    "E06_brainstorm", ["noise", "hypothetical"], "Name brainstorm (no decision)",
    [session("2026-09-24T20:00:00Z", [
        ("Just brainstorming, not deciding anything: names for a hypothetical side project, a habit tracker.", "Streakline, Tally, Kindle-less, Ritual, Loopkeeper."),
        ("more playful ones", "Habitat, Dailypop, Tickle, Nudgeling."),
        ("lol ok, not doing this now anyway", "Fair enough."),
    ])],
    traps=[TRAP("t1", "User decided/named a habit tracker project.", [r"habit", r"named|decid|chose|building|working on|start"], unless=[r"brainstorm|hypothetic|idea|consider|not (deciding|doing)"], why="explicit non-decision")],
))

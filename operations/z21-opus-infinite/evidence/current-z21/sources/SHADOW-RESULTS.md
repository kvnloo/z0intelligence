Z21 shadow results: (a) per-turn effort and (b) non-interactive routing. Labeler: local qwen3.5-4b on :11540 only (owner traces stay local).
as of 2026-11-07 17:18 CST. Owner: hypothesis. Shadow only; nothing changed live.
Code: kvnloo/z0intelligence feat/z21-shadows @ 3b9de8dc (z0int.z21_shadow). Data: ~/.z0int/shadow/z21/. Queries: calib.py, families.py (here). Outputs: calib-out.txt, families-7d.json, *-summary.json.

## (a) per-turn effort: no signal, dropped as built
- 2,978 sampled Opus turns, all labelled (13 unparsed -> high).
- Weight by label: low 95.1%, medium 3.3%, high 1.6%.
- Check against the real Opus turn that followed (state.db mode=ro). Turn chars p25/p50/p75:
  - low 540/844/1421
  - medium 653/1004/1486
  - high 207/464/1180
- "high" turns are the smallest. Action mix is ~75% exec in every label.
- So the labels do not order turns by work, and no effort saving is claimed.
- Why: the input is only the last tool/user message (<=2k chars, context_chars=0). A 4b model reads that as mechanical.
- Next version would need the owner ask plus the prior assistant turn; then re-run this same calib.

## (b) non-interactive routing: labels follow the template, not the task
- 2,919 sessions, 537 M weighted (~26% of the 7 d Opus meter), all labelled.
- Weight by label: sonnet 60.9%, opus 22.6%, haiku 16.5%. 321 unparsed (11%) fell back to opus.
- Only 275 distinct 60-char prefixes cover the 2,919 sessions, and the top 5 templates hold 48% of the weight.
- Same template gives the same label. For example:
  - dash-turbo skill preamble: 289 of 289 sonnet
  - kanban: 100% sonnet
  - critic-panel personas: 88% opus
- Labels run inverse to work: "haiku" sessions p50 54 Opus calls, "opus" sessions p50 1 call.
- So per-session 4b routing is not usable as-is. The weight is concentrated in a few callers, so a per-template (per-caller) decision is the cheaper lever. That needs an A/B per caller, not a label.

## Families that carry the weight (families.py, 7 d)
| family | sessions | Opus calls | weighted | % 7 d Opus meter | cache-write share | p50 prompt/session |
|---|---|---|---|---|---|---|
| critic-panel oneshot (StreetIQ) | 2,068 | 2,077 | 79.8 M | 3.80% | 85.7% | 42,836 |
| dash-turbo-live-preview cron | 289 | 3,553 | 81.7 M | 3.89% | 47.8% | 578,420 |
| oss-factory-kitchen cron | 49 | 2,652 | 55.6 M | 2.65% | 21.7% | 6.19 M |

Kitchen cron is not re-queued: 010 was rejected by analyst (its runs do real judgment work).

## Handed to analyst
- 012: critic-panel harness overhead (context, not model). Rank 1.
- 013: dash-turbo cron on Opus. Rank 2, low confidence.

## (d) tool-output trim
Not started. Owner-path outputs would need d1/local labelling; there is no labeler arm yet.

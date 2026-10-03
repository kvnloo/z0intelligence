# Bend to z0 architecture seam

- **What:** Where Bend belongs in z0intelligence and how AODL enforceability carries down into z0 decisions such as model choice. Contains a draft RFC (`RFC-bend-z0-seam.md`), three competing designs (`design-law-first.md`, `design-perf-first.md`, `design-evolution-first.md`), evidence notes (`bend-docs.md`, `aodl-bend.md`, `z0-decisions.md`) and the workflow script that produced them.
- **When:** 2026-10-03.
- **How:** Workflow `bend-z0-architecture-seam` (script in this folder). Three designer agents each wrote one design, three judge agents ranked them, and a synthesis agent wrote the RFC.
- **Verdict:** All three judges ranked law-first first. The draft RFC says Bend is z0's law layer, not a runtime component: the owner writes laws about a small, pure reference monitor `admit_F(...) -> Verdict`, and bend-native checks the proofs. It is a draft for owner review and nothing is implemented yet.
- **Code branches:** z0intelligence `exp/bend-aodl-gate`, `integrate/wiring-20261003`; kvnloo/bend patched fork; kvnloo/bend-native `exp/*-20261002`. The RFC pin table has the exact commits.
- **Not included:** `bend-seam/{src,scratch}` (a Bend checkout and issue dumps, 384 MB).

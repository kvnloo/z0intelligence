Exact historical source bytes for the three native baseline attempts.

live_baseline_01.py is recovered from Git commit
74af422e51ffab78d8a5521186cf50c9b8f2c042.

live_baseline_02.py was not saved before the output-cap parameter edit. It was
reconstructed by reversing precisely that edit. Its complete bytes hash to
eef8549cbf5bc423a75c2106d43db121791bde1b8f665089a0832476064bdaa0,
identical to the pre-run baseline02 freeze. This is exact-byte recovery, not a
claim that merely equivalent current behavior supplies historical source.

live_baseline_03.py is the source used for the separately frozen 4096-token
resource-adequacy attempt. Its full digest matches that run's freeze.

The independent checker remained unchanged across all three attempts. Shared
selection/replay helpers are archived for the initial and corrected admission
versions. manifest.json records all file digests and acquisition/recovery modes.
These files are archival source snapshots; use the main directory's current
driver for execution rather than invoking an isolated archived module.

Independent offline validation is in ../three-run-independent-replay.json and
../replay-validation.json. Original live receipts were not rewritten. The first
run's historical resource_comparison_eligible=true is preserved and explicitly
annotated as insufficient because complete context was absent.

The original producer stored captured parsed request objects and a raw-byte
digest, but did not preserve the corresponding raw HTTP bytes. Parsed request
context, forwarding constraints, source bindings, usage and independent outcome
can be replayed. Raw-byte digest verification remains UNKNOWN; no new network
execution or cryptographic provider attestation is implied.

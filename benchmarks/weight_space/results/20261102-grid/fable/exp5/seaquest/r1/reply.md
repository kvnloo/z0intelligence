## Design

**Strategy.** The sub never moves horizontally: it stays at column 5 facing left (its start pose), so its bullets sweep columns 4→0 of its own row and anything entering that "kill zone" from either direction dies if we keep firing. All enemies/bullets move horizontally, so a vertical step is a perfect dodge. The policy is:

1. **Dive** from the surface (row 0) when row 1's right half is clear.
2. **Hunt**: move up/down toward rows containing a target (enemy in columns 0‑3, or a diver anywhere; divers weighted 1.5×, nearer rows weighted more), while rows containing a threat (enemy/bullet in columns 5‑8 at distance 1, and anything near column 5 in an adjacent row) repel.
3. **Hold & fire** when a target/diver is ahead in my own row.
4. **Dodge** (strong vertical move) when a danger is at columns 6‑7 of my row, an enemy bullet at columns 2‑4, or an enemy at column 4 while my shot is on cooldown (detected by my own bullet being visible anywhere). Default dodge is up (down in row 1); adjacent‑row repulsion flips the direction.
5. **Surface** when the oxygen bar is below 3 cells and the diver bar shows ≥1 diver (both readable unambiguously from gauge cells 2 and 8). "Up" from row 1 is blocked unless a diver is certain (gauge col 8 lit while some col 0‑7 unlit).

**Hidden units (layer 1).** Units 0‑7: per‑row "hunt direction" h_r = tanh(signed, distance‑weighted sum of targets above minus below, with threat repulsion), gated to ≈0 offset only when the player is in row r (ch0 row sum = 2 → +60, bias −60; otherwise saturated at −1). Units 8‑15: per‑row dodge trigger dz_r (same gating, threshold logic on danger cells + my-bullet-in-flight). Units 16‑23: per‑row "target ahead in my row" TR_r. Unit 24: at row 0; 25: at row 0 and row 1 clear; 26: in row 1; 27: diver certain; 28: surface‑now (oxygen<60 & diver). Units 29‑31 unused.

**Layer 2** sums each per‑row family (the 7 inactive rows contribute a constant −7 which the bias cancels) to get U (hunt up‑ness, 0 at surface), DZU/DZD (dodge, dodge‑in‑row‑1), G1 (block up in row 1 without a certain diver), dive, surface, and target‑ahead signals. **Layer 3** combines them: fire ≈0 idle (8 with target ahead), up/down ≈ −4 idle, ±8·U for hunting, +17 for dodges, +16 for surfacing, +12 for diving, −10 gate; left/right/noop are never chosen.

```edits
# ================= Layer 1 =================
# ---- hunt units h_r: unit j=r-1 for rows r=1..8 ----
b1[0:8] = -60
W1[60:120:6, 0] = 30
W1[120:180:6, 1] = 30
W1[180:240:6, 2] = 30
W1[240:300:6, 3] = 30
W1[300:360:6, 4] = 30
W1[360:420:6, 5] = 30
W1[420:480:6, 6] = 30
W1[480:540:6, 7] = 30
# unit 0 (row 1): all other rows are below
W1[122:180:6, 0] = -3.75
W1[121:145:6, 0] = 1.25
W1[145, 0] = 8.75
W1[151:163:6, 0] = 11.25
W1[163:175:6, 0] = 6.25
W1[175, 0] = 3.75
W1[182:240:6, 0] = -3.0
W1[181:205:6, 0] = 1.0
W1[205:240:6, 0] = 3.0
W1[242:300:6, 0] = -2.25
W1[241:265:6, 0] = 0.75
W1[265:300:6, 0] = 2.25
W1[302:360:6, 0] = -1.8
W1[301:325:6, 0] = 0.6
W1[325:360:6, 0] = 1.8
W1[362:420:6, 0] = -1.5
W1[361:385:6, 0] = 0.5
W1[385:420:6, 0] = 1.5
W1[422:480:6, 0] = -1.2
W1[421:445:6, 0] = 0.4
W1[445:480:6, 0] = 1.2
W1[482:540:6, 0] = -1.05
W1[481:505:6, 0] = 0.35
W1[505:540:6, 0] = 1.05
# unit 1 (row 2)
W1[62:120:6, 1] = 3.75
W1[61:85:6, 1] = -1.25
W1[85, 1] = -8.75
W1[91:103:6, 1] = -11.25
W1[103:115:6, 1] = -6.25
W1[115, 1] = -3.75
W1[182:240:6, 1] = -3.75
W1[181:205:6, 1] = 1.25
W1[205, 1] = 8.75
W1[211:223:6, 1] = 11.25
W1[223:235:6, 1] = 6.25
W1[235, 1] = 3.75
W1[242:300:6, 1] = -3.0
W1[241:265:6, 1] = 1.0
W1[265:300:6, 1] = 3.0
W1[302:360:6, 1] = -2.25
W1[301:325:6, 1] = 0.75
W1[325:360:6, 1] = 2.25
W1[362:420:6, 1] = -1.8
W1[361:385:6, 1] = 0.6
W1[385:420:6, 1] = 1.8
W1[422:480:6, 1] = -1.5
W1[421:445:6, 1] = 0.5
W1[445:480:6, 1] = 1.5
W1[482:540:6, 1] = -1.2
W1[481:505:6, 1] = 0.4
W1[505:540:6, 1] = 1.2
# unit 2 (row 3)
W1[62:120:6, 2] = 3.0
W1[61:85:6, 2] = -1.0
W1[85:120:6, 2] = -3.0
W1[122:180:6, 2] = 3.75
W1[121:145:6, 2] = -1.25
W1[145, 2] = -8.75
W1[151:163:6, 2] = -11.25
W1[163:175:6, 2] = -6.25
W1[175, 2] = -3.75
W1[242:300:6, 2] = -3.75
W1[241:265:6, 2] = 1.25
W
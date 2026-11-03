## Design

**Strategy.** The submarine lives in column 8, facing left, with its back cell in column 9. From there, every enemy in its row at columns 0–7 is in front of it, so firing kills it whichever way it moves. The only threats are horizontal, so the danger always comes from the column-9 side or from bullets flying in from the left.

- **Setup at the surface (row 0).** Row 0 is completely safe. The sub moves right until its front is at column 9, then presses left once to reach column 8 facing left, then dives.
- **Below the surface.** The policy never chooses left or right, so the column and facing stay fixed.
- **Targeting.** The target is the deepest row (2–8) containing an enemy or bullet at columns 0–9, or a diver at columns 7–9.
  - The sub moves down or up toward that row and fires when it is there.
  - If its own row has a target, it fires first.
  - With no targets, it waits in row 4.
- **Safety.**
  - A row is "dangerous" if it has a danger-channel object at columns 7–9, or a bare bullet at column 6.
  - It never moves into a dangerous row.
  - If its own row becomes dangerous, it dodges: down if possible, otherwise up.
- **Oxygen.** When oxygen is below 60 and at least one diver is aboard, it surfaces, refills, and dives again. The diver-bar cell (row 9, column 8) is readable at that point, because the oxygen bar no longer covers it.

**Layer 1 (32 tanh units).**

| Units | What each computes |
|---|---|
| 0–7 | Y_k = [sub row ≥ k], for k = 1..8 |
| 8–14 | T_k = [any target in rows ≥ k], for k = 2..8 |
| 15–22 | G_d = [danger near column 8 in row d], for d = 1..8 |
| 23 | Surf = diver aboard AND oxygen low |
| 24 | H = at home on the surface (back cell at row 0, column 9) |
| 25 | N = front cell at row 0, column 9 |
| 26–31 | F_r = sub in row r AND a target in row r, for r = 2..7 |

**Layer 2 (32 units).**

| Units | What each computes |
|---|---|
| 0–7 | Bd_d = G_d AND sub row = d−1 (blocks down) |
| 8–14 | Ba_d = G_d AND sub row = d+1 (blocks up) |
| 15–22 | Bo_d = G_d AND sub row = d (dodge) |
| 23 | DN = target row > sub row (linear comparison of ΣT against ΣY; with no target, row 4 is used) |
| 24 | UP = target row < sub row (same comparison) |
| 25 | TGT = any target exists |
| 26 | Surf (copy) |
| 28 | N (copy) |
| 29 | R = on the surface, not home, front not at column 9 |
| 31 | FOWN = OR of the F_r units |

**Output.** Logits are priority-weighted sums, with large negative values for blocked moves:
- Dodge has the highest priority.
- Next comes surfacing, then firing at a target in the sub's own row.
- Next comes the move toward the target (or firing at it when already in its row).
- No-op (logit 10) is the default.
- Left and right are only possible on the surface.

```edits
# ---------- Layer 1 ----------
# Y_k = [sub row >= k], units 0..7
W1[60:600:6, 0] = 4
W1[120:600:6, 1] = 4
W1[180:600:6, 2] = 4
W1[240:600:6, 3] = 4
W1[300:600:6, 4] = 4
W1[360:600:6, 5] = 4
W1[420:600:6, 6] = 4
W1[480:600:6, 7] = 4
b1[0:8] = -4
# T_k = [target in rows >= k], k=2..8 -> units 8..14
W1[121:540:6, 8] = 8
W1[164:540:60, 8] = 8
W1[170:540:60, 8] = 8
W1[176:540:60, 8] = 8
W1[181:540:6, 9] = 8
W1[224:540:60, 9] = 8
W1[230:540:60, 9] = 8
W1[236:540:60, 9] = 8
W1[241:540:6, 10] = 8
W1[284:540:60, 10] = 8
W1[290:540:60, 10] = 8
W1[296:540:60, 10] = 8
W1[301:540:6, 11] = 8
W1[344:540:60, 11] = 8
W1[350:540:60, 11] = 8
W1[356:540:60, 11] = 8
W1[361:540:6, 12] = 8
W1[404:540:60, 12] = 8
W1[410:540:60, 12] = 8
W1[416:540:60, 12] = 8
W1[421:540:6, 13] = 8
W1[464:540:60, 13] = 8
W1[470:540:60, 13] = 8
W1[476:540:60, 13] = 8
W1[481:540:6, 14] = 8
W1[524:540:60, 14] = 8
W1[530:540:60, 14] = 8
W1[536:540:60, 14] = 8
b1[8:15] = -4
# G_d = danger near column 8 in row d, units 15..22
W1[103:120:6, 15] = 60
W1[97, 15] = 54
W1[98, 15] = -36
W1[163:180:6, 16] = 60
W1[157, 16] = 54
W1[158, 16] = -36
W1[223:240:6, 17] = 60
W1[217, 17] = 54
W1[218, 17] = -36
W1[283:300:6, 18] = 60
W1[277, 18] = 54
W1[278, 18] = -36
W1[343:360:6, 19] = 60
W1[337, 19] = 54
W1[338, 19] = -36
W1[403:420:6, 20] = 60
W1[397, 20] = 54
W1[398, 20] = -36
W1[463:480:6, 21] = 60
W1[457, 21] = 54
W1[458, 21] = -36
W1[523:540:6, 22] = 60
W1[517, 22] = 54
W1[518, 22] = -36
b1[15:23] = -21
# Surf (diver aboard and oxygen < 60), H (home at surface), N (front at col 9 at surface)
W1[593, 23] = 8
W1[557, 23] = -8
b1[23] = -4
W1[58, 24] = 8
b1[24] = -4
W1[54, 25] = 8
W1[58, 25] = -8
b1[25] = -4
# F_r = sub in row r and target in row r, r=2..7 -> units 26..31
W1[120:180:6, 26] = 20
W1[121:180:6, 26] = 8
W1[164:180:6, 26] = 8
W1[180:240:6, 27] = 20
W1[181:240:6, 27] = 8
W1[224:240:6, 27] = 8
W1[240:300:6, 28] = 20
W1[241:300:6, 28] = 8
W1[284:300:6, 28] = 8
W1[300:360:6, 29] = 20
W1[301:360:6, 29] = 8
W1[344:360:6, 29] = 8
W1[360:420:6, 30] = 20
W1[361:420:6, 30] = 8
W1[404:420:6, 30] = 8
W1[420:480:6, 31] = 20
W1[421:480:6, 31] = 8
W1[464:480:6, 31] = 8
b1[26:32] = -44
# ---------- Layer 2 ----------
# Bd_d = G_d and y=d-1 (block down), units 0..7
W2[15, 0] = 6
W2[0, 0] = -6
b2[0] = -6
W2[16, 1] = 6
W2[0, 1] = 6
W2[1, 1] = -6
W2[17, 2] = 6
W2[1, 2] = 6
W2[2, 2] = -6
W2[18, 3] = 6
W2[2, 3] = 6
W2[3, 3] = -6
W2[19, 4] = 6
W2[3, 4] = 6
W2[4, 4] = -6
W2[20, 5] = 6
W2[4, 5] = 6
W2[5, 5] = -6
W2[21, 6] = 6
W2[5, 6] = 6
W2[6, 6] = -6
W2[22, 7] = 6
W2[6, 7] = 6
W2[7, 7] = -6
b2[1:8] = -12
# Ba_d = G_d and y=d+1 (block up), units 8..14
W2[15, 8] = 6
W2[1, 8] = 6
W2[2, 8] = -6
W2[16, 9] = 6
W2[2, 9] = 6
W2[3, 9] = -6
W2[17, 10] = 6
W2[3, 10] = 6
W2[4, 10] = -6
W2[18, 11] = 6
W2[4, 11] = 6
W2[5, 11] = -6
W2[19, 12] = 6
W2[5, 12] = 6
W2[6, 12] = -6
W2[20, 13] = 6
W2[6, 13] = 6
W2[7, 13] = -6
b2[8:14] = -12
W2[21, 14] = 6
W2[7, 14] = 6
b2[14] = -6
# Bo_d = G_d and y=d (dodge), units 15..22
W2[15, 15] = 6
W2[0, 15] = 6
W2[1, 15] = -6
W2[16, 16] = 6
W2[1, 16] = 6
W2[2, 16] = -6
W2[17, 17] = 6
W2[2, 17] = 6
W2[3, 17] = -6
W2[18, 18] = 6
W2[3, 18] = 6
W2[4, 18] = -6
W2[19, 19] = 6
W2[4, 19] = 6
W2[5, 19] = -6
W2[20, 20] = 6
W2[5, 20] = 6
W2[6, 20] = -6
W2[21, 21] = 6
W2[6, 21] = 6
W2[7, 21] = -6
b2[15:22] = -12
W2[22, 22] = 6
W2[7, 22] = 6
b2[22] = -6
# DN (unit 23) and UP (unit 24)
W2[0:8, 23] = -6
W2[8, 23] = -12
W2[9:15, 23] = 6
b2[23] = 18
W2[0:8, 24] = 6
W2[8, 24] = 12
W2[9:15, 24] = -6
b2[24] = -30
# TGT, SU, NN, R, FOWN
W2[8, 25] = 6
W2[23, 26] = 6
W2[25, 28] = 6
W2[0, 29] = -12
W2[24, 29] = -12
W2[25, 29] = -12
b2[29] = -18
W2[26:32, 31] = 6
b2[31] = 30
# ---------- Output ----------  actions: 0 noop, 1 left, 2 up, 3 right, 4 down, 5 fire
W3[0:8, 4] = -100
W3[8:15, 2] = -100
W3[15:23, 2] = 35
W3[15:22, 4] = 40
W3[23, 4] = 12.5
W3[23, 5] = -12.5
W3[24, 2] = 12.5
W3[24, 5] = -12.5
W3[25, 5] = 12.5
W3[26, 2] = 37.5
W3[28, 1] = 50
W3[29, 3] = 50
W3[31, 5] = 17.5
b3[0:6] = [10, 10, -370, 10, -507.5, 5]
```
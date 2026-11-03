## Design

**Strategy.** The player never leaves column 5, where it starts. It only moves up and down, and lets enemies and treasure cross its column. Left, right and fire get logit −30, so they are never chosen, and sticky repeats can never move the player sideways. Since the column is fixed, every danger and reward can be read at fixed columns. Each row holds at most one entity (row = slot+1), so the trail channel tells the direction unambiguously.

Each step, the network scores three options (stay, go up a row, go down a row) by the state of the row the player would end up in:
- **D (deadly, −4):** an enemy is in column 5, or one column away and approaching.
- **S (soon, −0.7):** an approaching enemy is 2 or 3 columns away. This makes the player dodge early, which helps against sticky actions and the fast late game.
- **G (gold, +1 stay / +0.9 adjacent):** an approaching treasure, or one already in column 5. A gold row is automatically free of enemies.
- **Distant gold (+0.6 down to +0.45):** treasure further away in that direction draws the player toward it.
- **Small extras:**
  - a penalty for leaving your own gold row;
  - a small penalty when the row after the target is deadly, because a sticky repeat could carry you into it;
  - a mild pull toward the middle rows and a small bonus for staying.

**Layer 1 (27 units used).**
- **Units 0–2:** the player's row in binary (y−1, 3 bits), each saturated to ±1.
- **Units 3–10, D_r:** for rows 1–8, a threshold unit on enemy weights (col4 1, col5 2, col6 1) and trail weights (col3 +1, col5 −1, col7 +1). Approaching enemies score 2; receding enemies and treasure trails score at most 1. Everything is multiplied by 6, with bias −9.
- **Units 11–18, S_r:** the same idea for enemies 2–3 columns away; trails on the approaching side count + and trails on the receding side count −.
- **Units 19–26, G_r:** treasure weights `[1,1,2,2,3,4,3,2,2,1]` plus trail weights `[0,−1,−1,−2,−2,−3,−2,−2,−1,−1]`. Every approaching treasure scores 1 or more and every receding one 0 or less (checked case by case), with threshold 0.5.

**Layer 2 (22 units used).** For each player row y there is a stay unit, an up unit and a down unit. Each one is gated by the 3 row bits: weights ±5 with bias −15, so only the units for the player's actual row are active, and the rest sit at −1. An active unit outputs tanh(score), where the score is the weighted sum of the D/S/G features above. The output layer adds 12 × unit to the matching logit, and the biases cancel the constant −1s from inactive units. At row 1 the up logit is −12, and at row 8 the down logit is −12.

```edits
# ===== Layer 1 =====
# player row bits (player always in column 5): b0,b1,b2 of (row-1)
W1[30:600:60, 0] = [-4, -4, 4, -4, 4, -4, 4, -4, 4, -4]
W1[30:600:60, 1] = [-4, -4, -4, 4, 4, -4, -4, 4, 4, -4]
W1[30:600:60, 2] = [-4, -4, -4, -4, -4, 4, 4, 4, 4, -4]
# D_r units 3..10 (enemy ch1, trail ch4)
W1[61:120:6, 3] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[64:120:6, 3] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[121:180:6, 4] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[124:180:6, 4] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[181:240:6, 5] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[184:240:6, 5] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[241:300:6, 6] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[244:300:6, 6] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[301:360:6, 7] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[304:360:6, 7] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[361:420:6, 8] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[364:420:6, 8] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[421:480:6, 9] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[424:480:6, 9] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
W1[481:540:6, 10] = [0, 0, 0, 0, 6, 12, 6, 0, 0, 0]
W1[484:540:6, 10] = [0, 0, 0, 6, 0, -6, 0, 6, 0, 0]
b1[3:11] = -9
# S_r units 11..18
W1[61:120:6, 11] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[64:120:6, 11] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[121:180:6, 12] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[124:180:6, 12] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[181:240:6, 13] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[184:240:6, 13] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[241:300:6, 14] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[244:300:6, 14] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[301:360:6, 15] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[304:360:6, 15] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[361:420:6, 16] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[364:420:6, 16] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[421:480:6, 17] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[424:480:6, 17] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
W1[481:540:6, 18] = [0, 0, 6, 6, 0, 0, 0, 6, 6, 0]
W1[484:540:6, 18] = [0, 6, 6, -6, -6, 0, -6, -6, 6, 6]
b1[11:19] = -9
# G_r units 19..26 (gold ch2, trail ch4)
W1[62:120:6, 19] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[64:120:6, 19] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[122:180:6, 20] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[124:180:6, 20] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[182:240:6, 21] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[184:240:6, 21] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[242:300:6, 22] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[244:300:6, 22] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[302:360:6, 23] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[304:360:6, 23] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[362:420:6, 24] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[364:420:6, 24] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[422:480:6, 25] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[424:480:6, 25] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
W1[482:540:6, 26] = [6, 6, 12, 12, 18, 24, 18, 12, 12, 6]
W1[484:540:6, 26] = [0, -6, -6, -12, -12, -18, -12, -12, -6, -6]
b1[19:27] = -3

# ===== Layer 2: gates (stay 0..7 rows1-8, up 8..14 rows2-8, down 15..21 rows1-7) =====
W2[0:3, 0:8] = [[-5, 5, -5, 5, -5, 5, -5, 5], [-5, -5, 5, 5, -5, -5, 5, 5], [-5, -5, -5, -5, 5, 5, 5, 5]]
W2[0:3, 8:15] = [[5, -5, 5, -5, 5, -5, 5], [-5, 5, 5, -5, -5, 5, 5], [-5, -5, -5, 5, 5, 5, 5]]
W2[0:3, 15:22] = [[-5, 5, -5, 5, -5, 5, -5], [-5, -5, 5, 5, -5, -5, 5], [-5, -5, -5, -5, 5, 5, 5]]

# stay units: own row D (-2), S (-0.35), G (+0.5)
W2[3, 0] = -2
W2[4, 1] = -2
W2[5, 2] = -2
W2[6, 3] = -2
W2[7, 4] = -2
W2[8, 5] = -2
W2[9, 6] = -2
W2[10, 7] = -2
W2[11, 0] = -0.35
W2[12, 1] = -0.35
W2[13, 2] = -0.35
W2[14, 3] = -0.35
W2[15, 4] = -0.35
W2[16, 5] = -0.35
W2[17, 6] = -0.35
W2[18, 7] = -0.35
W2[19, 0] = 0.5
W2[20, 1] = 0.5
W2[21, 2] = 0.5
W2[22, 3] = 0.5
W2[23, 4] = 0.5
W2[24, 5] = 0.5
W2[25, 6] = 0.5
W2[26, 7] = 0.5

# up units: target row y-1, beyond row y-2, own gold, far gold above
W2[3, 8] = -2
W2[11, 8] = -0.35
W2[19, 8] = 0.45
W2[20, 8] = -0.5
W2[4, 9] = -2
W2[12, 9] = -0.35
W2[20, 9] = 0.45
W2[21, 9] = -0.5
W2[3, 9] = -0.25
W2[19, 9] = 0.30
W2[5, 10] = -2
W2[13, 10] = -0.35
W2[21, 10] = 0.45
W2[22, 10] = -0.5
W2[4, 10] = -0.25
W2[19:21, 10] = [0.285, 0.30]
W2[6, 11] = -2
W2[14, 11] = -0.35
W2[22, 11] = 0.45
W2[23, 11] = -0.5
W2[5, 11] = -0.25
W2[19:22, 11] = [0.27, 0.285, 0.30]
W2[7, 12] = -2
W2[15, 12] = -0.35
W2[23, 12] = 0.45
W2[24, 12] = -0.5
W2[6, 12] = -0.25
W2[19:23, 12] = [0.255, 0.27, 0.285, 0.30]
W2[8, 13] = -2
W2[16, 13] = -0.35
W2[24, 13] = 0.45
W2[25, 13] = -0.5
W2[7, 13] = -0.25
W2[19:24, 13] = [0.24, 0.255, 0.27, 0.285, 0.30]
W2[9, 14] = -2
W2[17, 14] = -0.35
W2[25, 14] = 0.45
W2[26, 14] = -0.5
W2[8, 14] = -0.25
W2[19:25, 14] = [0.225, 0.24, 0.255, 0.27, 0.285, 0.30]

# down units: target row y+1, beyond row y+2, own gold, far gold below
W2[4, 15] = -2
W2[12, 15] = -0.35
W2[20, 15] = 0.45
W2[19, 15] = -0.5
W2[5, 15] = -0.25
W2[21:27, 15] = [0.30, 0.285, 0.27, 0.255, 0.24, 0.225]
W2[5, 16] = -2
W2[13, 16] = -0.35
W2[21, 16] = 0.45
W2[20, 16] = -0.5
W2[6, 16] = -0.25
W2[22:27, 16] = [0.30, 0.285, 0.27, 0.255, 0.24]
W2[6, 17] = -2
W2[14, 17] = -0.35
W2[22, 17] = 0.45
W2[21, 17] = -0.5
W2[7, 17] = -0.25
W2[23:27, 17] = [0.30, 0.285, 0.27, 0.255]
W2[7, 18] = -2
W2[15, 18] = -0.35
W2[23, 18] = 0.45
W2[22, 18] = -0.5
W2[8, 18] = -0.25
W2[24:27, 18] = [0.30, 0.285, 0.27]
W2[8, 19] = -2
W2[16, 19] = -0.35
W2[24, 19] = 0.45
W2[23, 19] = -0.5
W2[9, 19] = -0.25
W2[25:27, 19] = [0.30, 0.285]
W2[9, 20] = -2
W2[17, 20] = -0.35
W2[25, 20] = 0.45
W2[24, 20] = -0.5
W2[10, 20] = -0.25
W2[26, 20] = 0.30
W2[10, 21] = -2
W2[18, 21] = -0.35
W2[26, 21] = 0.45
W2[25, 21] = -0.5

# layer-2 biases (gate -15 + feature offsets + centering)
b2[0:8] = [-17.15, -16.9, -16.55, -16.55, -16.55, -16.55, -16.9, -17.15]
b2[8:15] = [-17.7, -17.4, -16.915, -16.645, -16.39, -16.15, -16.125]
b2[15:22] = [-16.125, -16.15, -16.39, -16.645, -16.915, -17.4, -17.7]

# ===== Output =====
W3[0:8, 0] = 12
W3[8:15, 2] = 12
W3[15:22, 4] = 12
b3[0:6] = [84, -30, 72, -30, 72, -30]
```
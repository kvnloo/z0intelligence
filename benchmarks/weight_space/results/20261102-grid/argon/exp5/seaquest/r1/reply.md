### Policy Network Design & Strategy

#### 1. Game Strategy
- **Never Die at the Surface**: In Seaquest, moving into row 0 (`sub_y = 0`) after leaving the surface with `0` divers ends the episode immediately, while surfacing with `1..5` divers when oxygen is still high wastes a diver for `0` points and increases difficulty (`ramp_index`). Our policy locks out upward movement from row 1 (`GoUpFromRow[1]`) unless oxygen is low (`oxygen < 60`) **and** at least one diver is carried (`diver_count >= 1`). Note that in channel 5 (row 9), `oxygen_guage` fills from the left (`0 : oxygen*10//200`) while `diver_guage` fills from the right (`9 - diver_count : 9`), so when `oxygen < 60` (column 2 is `0`), column 8 is `1` **if and only if** `diver_count >= 1`.
- **Action Dominance (`f` over `n`)**: Firing (`f`, action 5) leaves `(sub_x, sub_y, sub_or)` unchanged and fires a bullet whenever `shot_timer == 0` with zero penalty, strictly dominating no-op (`n`, action 0). We suppress `n` (`b3[0] = -10`) and use `f` as the default hold-and-shoot action.
- **Row Safety & Opportunity Value (`RowVal[1..8]`)**: Since enemies, bullets, and divers only move horizontally within their row `r in 1..8`:
  - **Enemy bullets** have `ch1 = 1, ch2 = 0`; **enemy fish/subs** have `ch1 = 1, ch2 = 1`; **divers** have `ch1 = 0, ch2 = 1`.
  - By assigning `ch1` (danger) weight `-7.0` in the central columns `c = 3..6` and `-2.8` in the outer columns `c = 1, 2, 7, 8`, and `ch2` (reward) weight `+2.0` in `c = 3..6` and `+3.4` in `c = 1, 2, 7, 8`:
    - Any **enemy bullet** (`-2.8` to `-7.0`) or **central enemy** (`-5.0`) drives `RowVal[r]` to `-1.0` (dangerous).
    - A **distant enemy** in `c = 0..2, 7..9` (`+0.6`) with no central threat drives `RowVal[r]` to `+0.54` (safe shooting target).
    - A **diver** (`+2.0` to `+3.4`) with no threat drives `RowVal[r]` to `+0.99` (high-priority pickup).
- **Turn-and-Shoot vs. Full-Width Diver Collection (`RowHoriz[1..8]` + `SubX`)**:
  - In `RowHoriz[r]`, divers produce a saturating pull (`±3.0`), whereas enemies at safe shooting range (`c = 0..2` or `c = 7..9`) produce a moderate pull (`±0.8`, `tanh = ±0.664`), and close threats (`c = 3, 4` or `c = 5, 6`) push away.
  - Combined with `SubX` (`h1[24]`) in `GoLeftInRow[r]` and `GoRightInRow[r]`, a distant enemy on the left causes the submarine to step left (`l`, which sets `sub_or = False` to face left) until `sub_x = 3..4`, where `SubX` balances the moderate enemy pull so `f` (fire) takes over and shoots the enemy without walking into it. A diver's strong pull (`±0.995`) overrides `SubX` all the way to `c = 0` or `c = 9` to collect the diver, after which wall-avoidance units (`h1[29], h1[30]`) return the submarine to the center.

#### 2. Hidden Units Summary
- **Layer 1 (`h1[0..31]`)**:
  - `h1[0..7]` (`PlayerInRow[1..8]`): `+1` if `sub_y == r`, `-1` otherwise (from `ch 0` in row `r`).
  - `h1[8..15]` (`RowVal[1..8]`): Value of row `r` (`-1` = danger/bullet, `0` = empty, `+0.54` = shootable enemy, `+0.99` = diver).
  - `h1[16..23]` (`RowHoriz[1..8]`): Horizontal target/threat imbalance in row `r` (`< 0` = go/aim left, `> 0` = go/aim right).
  - `h1[24]` (`SubX`): Smooth horizontal submarine position from `ch 0` (`< 0` on left, `> 0` on right).
  - `h1[25]` (`SubAtSurface`): `+1` when `sub_y == 0`.
  - `h1[26..27]` (`NeedSurfaceLowOxy`, `UrgentSurfaceVeryLowOxy`): Detect `oxygen < 60` (and `< 40`) with `diver_count >= 1` from row 9 of `ch 5`.
  - `h1[28..31]`: Vertical centering (`h1[28]`), left wall `sub_x in 0..1` (`h1[29]`), right wall `sub_x in 8..9` (`h1[30]`), and underwater indicator (`h1[31]`).
- **Layer 2 (`h2[0..31]`)**:
  - `h2[0..7]` (`GoUpFromRow[1..8]`): Gated by `PlayerInRow[r]`; evaluates `+5.0 * RowVal[r-1] - 3.0 * RowVal[r]` plus multi-row lookahead and surfacing drive (`h2[0]` only fires on surfacing). Drives `u` (action 2).
  - `h2[8..14]` (`GoDownFromRow[1..7]`): Gated by `PlayerInRow[r]`; evaluates `+5.0 * RowVal[r+1] - 3.0 * RowVal[r]` plus multi-row lookahead. Drives `d` (action 4).
  - `h2[15..22]` (`GoLeftInRow[1..8]`): Gated by `PlayerInRow[r]`; combines `-3.5 * RowHoriz[r] + 1.2 * SubX` and right-wall recoil. Drives `l` (action 1) and `f` (action 5).
  - `h2[23..30]` (`GoRightInRow[1..8]`): Gated by `PlayerInRow[r]`; combines `+3.5 * RowHoriz[r] - 1.2 * SubX` and left-wall recoil. Drives `r` (action 3) and `f` (action 5).
  - `h2[31]` (`DiveFromSurface`): Drives `d` (action 4) when `sub_y == 0` and row 1 is safe.

```edits
# ==============================================================================
# LAYER 1: W1 (600 x 32) and b1 (32)
# ==============================================================================

# --- h1[0..7]: PlayerInRow[1..8] (ch 0 in rows 1..8) ---
b1[0:8] = -3.0
W1[60:120:6, 0] = 3.0
W1[120:180:6, 1] = 3.0
W1[180:240:6, 2] = 3.0
W1[240:300:6, 3] = 3.0
W1[300:360:6, 4] = 3.0
W1[360:420:6, 5] = 3.0
W1[420:480:6, 6] = 3.0
W1[480:540:6, 7] = 3.0

# --- h1[8..15]: RowVal[1..8] (ch 1 danger, ch 2 reward, ch 3 friendly bullet) ---
W1[61:120:6, 8] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[62:120:6, 8] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[63:120:6, 8] = -0.3

W1[121:180:6, 9] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[122:180:6, 9] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[123:180:6, 9] = -0.3

W1[181:240:6, 10] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[182:240:6, 10] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[183:240:6, 10] = -0.3

W1[241:300:6, 11] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[242:300:6, 11] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[243:300:6, 11] = -0.3

W1[301:360:6, 12] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[302:360:6, 12] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[303:360:6, 12] = -0.3

W1[361:420:6, 13] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[362:420:6, 13] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[363:420:6, 13] = -0.3

W1[421:480:6, 14] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[422:480:6, 14] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[423:480:6, 14] = -0.3

W1[481:540:6, 15] = [-2.2, -2.8, -2.8, -7.0, -7.0, -7.0, -7.0, -2.8, -2.8, -2.2]
W1[482:540:6, 15] = [2.8, 3.4, 3.4, 2.0, 2.0, 2.0, 2.0, 3.4, 3.4, 2.8]
W1[483:540:6, 15] = -0.3

# --- h1[16..23]: RowHoriz[1..8] (left < 0, right > 0 in rows 1..8) ---
W1[61:120:6, 16] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[62:120:6, 16] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[121:180:6, 17] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[122:180:6, 17] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[181:240:6, 18] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[182:240:6, 18] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[241:300:6, 19] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[242:300:6, 19] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[301:360:6, 20] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[302:360:6, 20] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[361:420:6, 21] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[362:420:6, 21] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[421:480:6, 22] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[422:480:6, 22] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

W1[481:540:6, 23] = [2.2, 2.2, 2.2, 4.5, 2.0, -2.0, -4.5, -2.2, -2.2, -2.2]
W1[482:540:6, 23] = [-3.0, -3.0, -3.0, -2.5, -1.2, 1.2, 2.5, 3.0, 3.0, 3.0]

# --- h1[24]: SubX (player horizontal position across cols 0..9 in ch 0) ---
W1[0:600:60, 24] = -2.0
W1[6:600:60, 24] = -1.5
W1[12:600:60, 24] = -1.0
W1[18:600:60, 24] = -0.5
W1[24:600:60, 24] = -0.2
W1[30:600:60, 24] = 0.2
W1[36:600:60, 24] = 0.5
W1[42:600:60, 24] = 1.0
W1[48:600:60, 24] = 1.5
W1[54:600:60, 24] = 2.0

# --- h1[25]: SubAtSurface (sub_y == 0) ---
b1[25] = -3.0
W1[0:60:6, 25] = 3.0

# --- h1[26]: NeedSurfaceLowOxy (oxygen < 60 AND diver_count >= 1) ---
b1[26] = -2.0
W1[557, 26] = -4.0
W1[551, 26] = -2.0
W1[593, 26] = 5.0

# --- h1[27]: UrgentSurfaceVeryLowOxy (oxygen < 40 AND diver_count >= 1) ---
b1[27] = -2.0
W1[557, 27] = -4.0
W1[551, 27] = -4.0
W1[593, 27] = 5.0

# --- h1[28]: SubVerticalCenter (rows 1..3 < 0, rows 6..8 > 0) ---
W1[60:180:6, 28] = -1.0
W1[180:240:6, 28] = -0.4
W1[360:420:6, 28] = 0.4
W1[420:540:6, 28] = 1.0

# --- h1[29]: SubNearLeftWall (sub_x in 0..1) ---
b1[29] = -2.5
W1[0:600:60, 29] = 2.5
W1[6:600:60, 29] = 2.0

# --- h1[30]: SubNearRightWall (sub_x in 8..9) ---
b1[30] = -2.5
W1[48:600:60, 30] = 2.0
W1[54:600:60, 30] = 2.5

# --- h1[31]: Underwater indicator (sub_y >= 1) ---
b1[31] = 2.0
W1[0:60:6, 31] = -2.5

# ==============================================================================
# LAYER 2: W2 (32 x 32) and b2 (32)
# ==============================================================================

# --- h2[0]: GoUpFromRow[1] (Surface ONLY when NeedSurfaceLowOxy is active) ---
b2[0] = -10.5
W2[0, 0] = 8.0
W2[26, 0] = 4.0
W2[27, 0] = 3.0

# --- h2[1..7]: GoUpFromRow[2..8] ---
b2[1:5] = -8.2
b2[5:8] = -7.85
W2[1, 1] = 10.0
W2[2, 2] = 10.0
W2[3, 3] = 10.0
W2[4, 4] = 10.0
W2[5, 5] = 10.0
W2[6, 6] = 10.0
W2[7, 7] = 10.0
W2[26, 1:8] = 1.4
W2[27, 1:8] = 1.4

# Row 2 -> 1
W2[9, 1] = -3.0
W2[8, 1] = 5.0
# Row 3 -> 2
W2[10, 2] = -3.0
W2[9, 2] = 5.0
W2[8, 2] = 0.6
# Row 4 -> 3
W2[11, 3] = -3.0
W2[10, 3] = 5.0
W2[9, 3] = 0.6
W2[8, 3] = 0.4
# Row 5 -> 4
W2[12, 4] = -3.0
W2[11, 4] = 5.0
W2[10, 4] = 0.6
W2[9, 4] = 0.4
W2[8, 4] = 0.2
# Row 6 -> 5
W2[13, 5] = -3.0
W2[12, 5] = 5.0
W2[11, 5] = 0.6
W2[10, 5] = 0.4
W2[9, 5] = 0.2
# Row 7 -> 6
W2[14, 6] = -3.0
W2[13, 6] = 5.0
W2[12, 6] = 0.6
W2[11, 6] = 0.4
W2[10, 6] = 0.2
# Row 8 -> 7
W2[15, 7] = -3.0
W2[14, 7] = 5.0
W2[13, 7] = 0.6
W2[12, 7] = 0.4
W2[11, 7] = 0.2

# --- h2[8..14]: GoDownFromRow[1..7] ---
b2[8:11] = -12.15
b2[11:15] = -12.5
W2[0, 8] = 10.0
W2[1, 9] = 10.0
W2[2, 10] = 10.0
W2[3, 11] = 10.0
W2[4, 12] = 10.0
W2[5, 13] = 10.0
W2[6, 14] = 10.0
W2[26, 8:15] = -0.8
W2[27, 8:15] = -0.8

# Row 1 -> 2
W2[8, 8] = -3.0
W2[9, 8] = 5.0
W2[10, 8] = 0.6
W2[11, 8] = 0.4
W2[12, 8] = 0.2
# Row 2 -> 3
W2[9, 9] = -3.0
W2[10, 9] = 5.0
W2[11, 9] = 0.6
W2[12, 9] = 0.4
W2[13, 9] = 0.2
# Row 3 -> 4
W2[10, 10] = -3.0
W2[11, 10] = 5.0
W2[12, 10] = 0.6
W2[13, 10] = 0.4
W2[14, 10] = 0.2
# Row 4 -> 5
W2[11, 11] = -3.0
W2[12, 11] = 5.0
W2[13, 11] = 0.6
W2[14, 11] = 0.4
W2[15, 11] = 0.2
# Row 5 -> 6
W2[12, 12] = -3.0
W2[13, 12] = 5.0
W2[14, 12] = 0.6
W2[15, 12] = 0.4
# Row 6 -> 7
W2[13, 13] = -3.0
W2[14, 13] = 5.0
W2[15, 13] = 0.6
# Row 7 -> 8
W2[14, 14] = -3.0
W2[15, 14] = 5.0

# --- h2[15..22]: GoLeftInRow[1..8] ---
b2[15:23] = -10.6
W2[0, 15] = 10.0
W2[1, 16] = 10.0
W2[2, 17] = 10.0
W2[3, 18] = 10.0
W2[4, 19] = 10.0
W2[5, 20] = 10.0
W2[6, 21] = 10.0
W2[7, 22] = 10.0

W2[16, 15] = -3.5
W2[17, 16] = -3.5
W2[18, 17] = -3.5
W2[19, 18] = -3.5
W2[20, 19] = -3.5
W2[21, 20] = -3.5
W2[22, 21] = -3.5
W2[23, 22] = -3.5

W2[24, 15:23] = 1.2
W2[30, 15:23] = 1.2

# --- h2[23..30]: GoRightInRow[1..8] ---
b2[23:31] = -10.6
W2[0, 23] = 10.0
W2[1, 24] = 10.0
W2[2, 25] = 10.0
W2[3, 26] = 10.0
W2[4, 27] = 10.0
W2[5, 28] = 10.0
W2[6, 29] = 10.0
W2[7, 30] = 10.0

W2[16, 23] = 3.5
W2[17, 24] = 3.5
W2[18, 25] = 3.5
W2[19, 26] = 3.5
W2[20, 27] = 3.5
W2[21, 28] = 3.5
W2[22, 29] = 3.5
W2[23, 30] = 3.5

W2[24, 23:31] = -1.2
W2[29, 23:31] = 1.2

# --- h2[31]: DiveFromSurface (sub_y == 0 and row 1 is safe) ---
b2[31] = -3.0
W2[25, 31] = 5.0
W2[8, 31] = 3.0

# ==============================================================================
# LAYER 3: W3 (32 x 6) and b3 (6)
# Actions: 0='n', 1='l', 2='u', 3='r', 4='d', 5='f'
# ==============================================================================

b3[0:6] = [-10.0, 25.6, 32.6, 25.6, 32.6, 21.2]

# Up (action 2)
W3[0, 2] = 6.0
W3[1:8, 2] = 3.8

# Down (action 4)
W3[8:15, 4] = 3.8
W3[31, 4] = 6.0

# Left (action 1) & Fire (action 5)
W3[15:23, 1] = 3.2
W3[15:23, 5] = 1.2

# Right (action 3) & Fire (action 5)
W3[23:31, 3] = 3.2
W3[23:31, 5] = 1.2
```

### Design & Strategy

#### Game Mechanics & Optimal Tracking Strategy
In MinAtar Breakout, the ball moves diagonally by 1 cell per step. When it steps from row 8 into row 9 (`new_y == 9`), it bounces off the paddle if and only if the paddle's updated column `pos` (after taking the current action) equals either:
1. `ball_x` (the ball's column at row 8): reflects vertically (`[3, 2, 1, 0][ball_dir]`), preserving horizontal velocity.
2. `new_x` (the ball's next column at row 9): reflects both vertically and horizontally (`[2, 3, 0, 1][ball_dir]`), acting as a retroreflector.

A naive policy that always targets `ball_x` alternates between moving (`'l'`/`'r'`) and no-op (`'n'`), so sticky actions (probability `0.1` of repeating the previous `'n'`) cause it to miss at row 8. Conversely, a policy that always targets `new_x` always hits the retroreflector branch (`new_x == pos`), which reverses `(dx, dy) -> (-dx, -dy)` and traps the ball in periodic retroreflected loops once a tunnel to the ceiling is cleared.

Our policy combines both regimes:
- **Rows `0..7` (`ball_y < 8`) — Target `new_x`**:
  Across all columns `0..9` (including wall bounces at `0` and `9` and episode reset states where `ball_x == last_x`), the exact next ball column `new_x` is a single linear function of the one-hot ball column (`ball_x`) and trail column (`last_x`):
  $$\text{new\_x} = \sum_{c=0}^9 w_{\text{ball}}[c]\,\text{ball}[c] + \sum_{c=0}^9 w_{\text{trail}}[c]\,\text{trail}[c]$$
  with $w_{\text{ball}} = [1, 2, 4, 6, 8, 10, 12, 14, 16, 17]$ and $w_{\text{trail}} = [0, -1, -2, -3, -4, -5, -6, -7, -8, -9]$. Tracking `new_x` while `ball_y < 8` keeps the paddle moving continuously in the ball's horizontal direction (`'l'` or `'r'` without alternating `'n'`), so sticky actions repeat the same movement direction and the paddle arrives at `pos == ball_x` as the ball enters row 8.
- **Row `8` (`ball_y == 8`) — Target `ball_x`**:
  When the ball is at row 8, `pos` is already at `ball_x`, so targeting `ball_x` selects `'n'` (`pos == ball_x`, specular bounce) with probability `0.9`, while a sticky action (probability `0.1`) repeats the previous step's move into `pos == new_x` (retroreflector bounce). Both outcomes catch the ball, while the 90%/10% mix of specular and retroreflective bounces prevents infinite loops. If a wall-turnaround sticky action left the paddle 1 step behind entering row 8, targeting `ball_x` moves `pos` onto `ball_x` and still catches the ball.

#### Hidden Units
- **First hidden layer (`h1[0..3]`)**:
  - `h1[0]`: $\tanh(10(\text{new\_x} - \text{pos} - 0.5))$ for `ball_y < 8`, forced to $-1$ when `ball_y == 8`. Fires $+1$ iff `ball_y < 8` and $\text{new\_x} > \text{pos}$.
  - `h1[1]`: $\tanh(10(\text{pos} - \text{new\_x} - 0.5))$ for `ball_y < 8`, forced to $-1$ when `ball_y == 8`. Fires $+1$ iff `ball_y < 8` and $\text{new\_x} < \text{pos}$.
  - `h1[2]`: $\tanh(10(\text{ball\_x} - \text{pos} - 0.5))$ when `ball_y == 8`, gated to $-1$ when `ball_y < 8`. Fires $+1$ iff `ball_y == 8` and $\text{ball\_x} > \text{pos}$.
  - `h1[3]`: $\tanh(10(\text{pos} - \text{ball\_x} - 0.5))$ when `ball_y == 8`, gated to $-1$ when `ball_y < 8`. Fires $+1$ iff `ball_y == 8` and $\text{ball\_x} < \text{pos}$.
- **Second hidden layer (`h2[0..2]`)**:
  - `h2[0]`: OR-gate over `h1[0]` and `h1[2]` ($\approx +1$ when action should be `'r'`, $-1$ otherwise).
  - `h2[1]`: OR-gate over `h1[1]` and `h1[3]` ($\approx +1$ when action should be `'l'`, $-1$ otherwise).
  - `h2[2]`: NOR-gate over `h1[0..3]` ($\approx +1$ when action should be `'n'`, $-1$ otherwise).
- **Output layer (`logits`)**: Maps `h2[0]` to action `3` (`'r'`), `h2[1]` to action `1` (`'l'`), and `h2[2]` to action `0` (`'n'`), with scale `50`.

```edits
# h1[0]: ball_y < 8 and new_x - pos >= 1 (move right)
W1[3:60:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[63:120:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[123:180:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[183:240:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[243:300:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[303:360:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[363:420:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[423:480:6, 0] = [10, 20, 40, 60, 80, 100, 120, 140, 160, 170]
W1[483:540:6, 0] = -500
W1[4:60:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[64:120:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[124:180:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[184:240:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[244:300:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[304:360:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[364:420:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[424:480:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[484:540:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[544:600:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
W1[540:600:6, 0] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
b1[0] = -5

# h1[1]: ball_y < 8 and new_x - pos <= -1 (move left)
W1[3:60:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[63:120:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[123:180:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[183:240:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[243:300:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[303:360:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[363:420:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[423:480:6, 1] = [-10, -20, -40, -60, -80, -100, -120, -140, -160, -170]
W1[483:540:6, 1] = -500
W1[4:60:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[64:120:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[124:180:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[184:240:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[244:300:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[304:360:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[364:420:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[424:480:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[484:540:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[544:600:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
W1[540:600:6, 1] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
b1[1] = -5

# h1[2]: ball_y == 8 and ball_x - pos >= 1 (move right)
W1[483:540:6, 2] = [200, 210, 220, 230, 240, 250, 260, 270, 280, 290]
W1[540:600:6, 2] = [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]
b1[2] = -205

# h1[3]: ball_y == 8 and ball_x - pos <= -1 (move left)
W1[483:540:6, 3] = [200, 190, 180, 170, 160, 150, 140, 130, 120, 110]
W1[540:600:6, 3] = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
b1[3] = -205

# h2[0..2]: Right, Left, No-op detectors
W2[0:4, 0] = [10, 0, 10, 0]
b2[0] = 10
W2[0:4, 1] = [0, 10, 0, 10]
b2[1] = 10
W2[0:4, 2] = [-10, -10, -10, -10]
b2[2] = -30

# Output logits: 0=no-op, 1=left, 3=right
W3[2, 0] = 50
W3[1, 1] = 50
W3[0, 3] = 50
b3[2] = -50
b3[4] = -50
b3[5] = -50
```

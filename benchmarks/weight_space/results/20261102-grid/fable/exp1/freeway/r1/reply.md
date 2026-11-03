**Observations**

*Constant inputs / border.* Every input that is always 1 learns weights that are a copy of the first-layer bias (its gradient equals the bias gradient). b1 = `0.048 0.185 -0.041 -0.492 … 0.434 … 0.434 -0.220 0.195 0.083`, and the channel-3 rows of all border cells carry the same signature, e.g. input 3 (0,0,ch3) = `0.023 0.181 -0.081 -0.477 … 0.440 … 0.437 -0.217 0.198`, input 48 (0,9,ch3) = `0.100 0.268 -0.062 -0.598 …`, input 498 (9,9,ch3) = `0.112 0.168 -0.164 -0.408 …`. Interior ch3 rows (e.g. 108, 113, 118, 163, 218 …) are all tiny (|w|<0.15): there are never obstacles inside the arena.

*Crucial anomaly – row 1, channel 2.* The same "always-on" signature also appears on inputs 57, 62, 67, 72, 77, 82, 87, 92, i.e. (1,1,ch2) … (1,8,ch2): `0.050 0.215 -0.101 -0.550`, `0.020 0.232 0.001 -0.633`, `-0.058 0.120 -0.074 -0.497`, `0.057 0.202 -0.123 -0.468`, `0.176 0.177 0.032 -0.557`, `-0.018 0.247 -0.037 -0.541`, `0.069 0.087 0.115 -0.473`, `-0.031 0.177 -0.118 -0.473`. So the entire top playable row is permanently a reward target (a goal line). Channel 2 everywhere else (112, 117, 162, 217, 262, 312, 362, 412 …) is at initialisation scale – no other reward objects ever appear.

*Channel 0 (agent).* Large, cell-specific weights in rows 2–8, cols 1–8: input 120 (2,4) `4.331 -2.970 -1.338 … 3.311`, 225 (4,5) `-6.691 4.613 … 5.450`, 320 (6,4) `5.095 -3.877 3.016 … 5.309`, 325 (6,5) `4.252 … -6.134 … 4.529`, 425 (8,5) `-3.956 1.544 -3.865 … 4.941`. Row-1 agent inputs (55, 60, 65 …) are tiny: the agent is never observed standing in row 1 – reaching it ends the episode.

*Channel 1 (moving objects).* Large in rows 2–7 only: 111 (2,2) `-0.522 -0.497 -0.615 -1.480`, 161 (3,2) has 6.639 to unit 12, 181 (3,6) 6.689 to unit 12, 321 (6,4) 2.826/4.041 to units 7/15, 341 (6,8) 2.842/4.057 to the same units, 356 (7,1) `0.236 -0.387 … 0.703`. Row 8 ch1 (406 `0.092 0.032 0.037 -0.052`, 411, 416, 421 …) and row 1 ch1 are near zero: moving objects live in six lanes, rows 2–7, never in the agent's start row or the goal row. Several hidden units pool the same lane at different columns (unit 12 for row 3, unit 15 for row 6) – lane-occupancy detectors.

*Channel 4 (previous frame).* Also large and lane-structured: 239 (4,7) 6.440 → unit 16, 264 (5,2) 6.182 → unit 4, 294 (5,8) -4.103 → unit 18, 159 (3,1) -4.351 → unit 18, 194 (3,8) 5.245 → unit 13. Combined with ch1 this gives direction/speed of the lane traffic.

*Output layer.* b3 = `-0.029 3.142 -1.862 0.173 -1.269`: by default the policy goes UP, strongly dislikes DOWN and RIGHT. W3 column 1 (up) has big positive inputs from units 6 (2.626), 28 (2.473), 25 (2.235), 17 (2.194), 13 (1.898) and big negative ones from units 8 (-2.585) and 11 (-1.980); unit 11 simultaneously pushes no-op (+1.589) and left (+1.719) – a "car ahead, wait/sidestep" feature. Column 0 (no-op) is fed by units 1 (1.693), 26 (1.501), 16 (1.371).

**Reasoning**

A permanent reward line along the whole top row, an agent that starts (and is only ever seen) in rows 2–8, moving non-agent objects confined to horizontal lanes in rows 2–7, no interior obstacles, no collectible reward cells, and an output prior of "move up unless a lane detector fires" is the signature of a road-crossing game. Snake is ruled out (ch1 would track a body everywhere, ch2 would be a single food cell). Dodging/catching falling objects is ruled out (objects would occupy all rows including the agent's row and no fixed goal row would exist). Collect-the-dots/navigation with a single goal is ruled out because ch2 is never variable and ch1 is heavily used. Pong/Breakout-style play is ruled out by the absence of a paddle row and the 6-lane structure of ch1.

**Game**

Road-crossing game – Freeway (Atari/MinAtar) / Frogger-style lane crossing.

**Description**

The agent starts at the bottom row and must move up across six lanes of cars that scroll horizontally (rows 2–7), using no-op/left/right to wait or dodge; reaching the top row (row 1) gives the reward and ends the episode, while being hit by a car ends it (or resets the agent) without reward.

**Confidence:** 75%
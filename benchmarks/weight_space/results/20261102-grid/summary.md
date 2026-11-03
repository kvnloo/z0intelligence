# Weight-space programming evals: `20261102-grid`

Tasks, prompts and networks verbatim from the paper's released logs (RikiyaT/lm_understand-logs @ 4d09ba6);
one fresh, tool-free reply per cell; reasoning effort as in the paper (read: high, write: xhigh) unless the
arm is labelled otherwise.

## Read: name the game / shape from the raw weights (open answer)

| task | argon | astra | deepseek | fable | opus |
|---|---|---|---|---|---|
| exp1/breakout | ✅ 99% | ✅ 99% | ✅ 95% | ✅ 85% | ✅ 88% |
| exp1/freeway | ✅ 99% | ✅ 97% | ✅ 93% | ✅ 75% | ✅ 75% |
| exp1/pacman | ✅ 99% | ✅ 95% | ✅ 78% | ✅ 70% | ✅ 72% |
| exp1/pong | ✅ 99% | ✅ 97% | ∅ | ✅ 70% | ✅ 70% |
| exp2/circle | ✅ 95% | ✅ 86% | ✅ 80% | ✅ 62% | ✅ 45% |
| exp2/square | ✅ 95% | ✅ 82% | ❌ circle 68% | ✅ 60% | ✅ 50% |
| exp2/triangle | ✅ 99% | ✅ 84% | ❌ circle 82% | ✅ 55% | ✅ 60% |
| **total** | **7/7** | **7/7** | **4/7** | **7/7** | **7/7** |

✅ correct · ❌ named something else · ∅ no answer (output budget spent reasoning) · n% = stated confidence.

## Write: all 20,486 weights of a MinAtar policy from scratch, one reply, no tools

Mean score over 1,000 episodes of official MinAtar 1.0.15 (sticky actions 0.1, softmax sampling, 10k-step cap).

| game | PPO 100M / 500M (paper) | Astra (paper) | argon | astra | deepseek | fable | opus |
|---|---|---|---|---|---|---|---|
| asterix | 25.8 / 55.2 | 53.2 | 31.6 | 42.2 | 2.3 | ∅ (no weights) | 47.3 |
| breakout | 15.8 / 25.3 | 517.4 | 61.3 | 586.4 | 7.6 | ∅ (no weights) | ∅ (no weights) |
| seaquest | 5.6 / 88.9 | 53.1 | 1.8 | 43.2 | 0.0 | 0.1 | 46.2 |

∅ (no weights) = the reply had no `edits` block: the model spent its whole output budget (128k tokens on the API routes) reasoning.

## Cost per cell

| arm | task | effort | out tok | reasoning tok | wall s | finish |
|---|---|---|---|---|---|---|
| argon | exp1/breakout | high | 29470 | 24768 | 253.2 |  |
| argon | exp1/freeway | high | 40980 | 34787 | 357.6 |  |
| argon | exp1/pacman | high | 27543 | 24216 | 248.3 |  |
| argon | exp1/pong | high | 43166 | 39837 | 379.6 |  |
| argon | exp2/circle | high | 51518 | 46791 | 466.4 |  |
| argon | exp2/square | high | 103424 | 99548 | 927.4 |  |
| argon | exp2/triangle | high | 49284 | 44182 | 432.9 |  |
| argon | exp5/asterix | xhigh | 129850 | 121555 | 1376.3 |  |
| argon | exp5/breakout | xhigh | 105957 | 101704 | 1124.7 |  |
| argon | exp5/seaquest | xhigh | 122625 | 114829 | 1327.7 |  |
| astra | exp1/breakout | high | 1893 | 1168 | 59.7 |  |
| astra | exp1/freeway | high | 2410 | 1552 | 63.4 |  |
| astra | exp1/pacman | high | 1352 | 516 | 33.1 |  |
| astra | exp1/pong | high | 1502 | 774 | 59.6 |  |
| astra | exp2/circle | high | 1865 | 1383 | 67.0 |  |
| astra | exp2/square | high | 1930 | 1444 | 53.8 |  |
| astra | exp2/triangle | high | 2484 | 1887 | 84.1 |  |
| astra | exp5/asterix | xhigh | 26761 | 20941 | 942.6 |  |
| astra | exp5/breakout | xhigh | 28483 | 23717 | 852.3 |  |
| astra | exp5/seaquest | xhigh | 30561 | 26883 | 1020.6 |  |
| deepseek | exp1/breakout | high | 43382 | 42565 | 454.2 |  |
| deepseek | exp1/freeway | high | 38173 | 37143 | 383.2 |  |
| deepseek | exp1/pacman | high | 28944 | 28417 | 354.2 |  |
| deepseek | exp1/pong | high | 64000 | 64000 | 557.4 | length |
| deepseek | exp2/circle | high | 21881 | 21337 | 229.5 |  |
| deepseek | exp2/square | high | 57215 | 56406 | 572.8 |  |
| deepseek | exp2/triangle | high | 46592 | 46061 | 472.9 |  |
| deepseek | exp5/asterix | xhigh | 50826 | 47698 | 759.4 |  |
| deepseek | exp5/breakout | xhigh | 36834 | 36247 | 520.9 |  |
| deepseek | exp5/seaquest | xhigh | 81802 | 75807 | 1426.7 | stop |
| fable | exp1/breakout | high | 17779 | 15474 | 196.0 |  |
| fable | exp1/freeway | high | 25589 | 23591 | 276.7 |  |
| fable | exp1/pacman | high | 13696 | 11458 | 159.7 |  |
| fable | exp1/pong | high | 19112 | 17077 | 223.6 |  |
| fable | exp2/circle | high | 22730 | 19805 | 298.2 |  |
| fable | exp2/square | high | 42903 | 39956 | 482.7 |  |
| fable | exp2/triangle | high | 44611 | 41009 | 483.7 |  |
| fable | exp5/asterix | xhigh | 128000 | 128000 | 1671.0 | max_tokens |
| fable | exp5/breakout | xhigh | 128000 | 128000 | 1743.1 | max_tokens |
| fable | exp5/seaquest | xhigh | 128000 | 125691 | 1615.4 | max_tokens |
| opus | exp1/breakout | high | 11746 | 10252 | 97.5 |  |
| opus | exp1/freeway | high | 14563 | 13209 | 134.5 |  |
| opus | exp1/pacman | high | 8357 | 7021 | 72.1 |  |
| opus | exp1/pong | high | 16985 | 15477 | 157.8 |  |
| opus | exp2/circle | high | 6279 | 4401 | 65.0 |  |
| opus | exp2/square | high | 18855 | 17132 | 171.2 |  |
| opus | exp2/triangle | high | 19028 | 17344 | 165.2 |  |
| opus | exp5/asterix | xhigh | 63828 | 58225 | 593.3 |  |
| opus | exp5/breakout | xhigh | 128000 | 128000 | 1193.2 | max_tokens |
| opus | exp5/seaquest | xhigh | 117427 | 113381 | 1122.7 | end_turn |

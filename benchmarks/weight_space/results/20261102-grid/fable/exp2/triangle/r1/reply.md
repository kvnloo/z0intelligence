**Observations**

*Output biases b3.* Every pixel of row 0 (indices 0–11: −0.457 … −0.518), column 0 (12: −0.340, 24: −0.381, 36: −0.535, 48: −0.487, 60: −0.463, 72: −0.514, 84: −0.371, 96: −0.451, 108: −0.357, 120: −0.461, 132: −0.356) and column 11 (23: −0.482, 35: −0.389, 47: −0.532, 59: −0.510, 71: −0.406, 83: −0.504, …) is strongly negative, so the one‑pixel frame is always black. Row 11 is only moderately negative (−0.10 … −0.28 in the middle), row 1 only at its centre (17: −0.143, 18: −0.105). The interior (rows 2–10, cols 2–9) sits near 0 (e.g. 63: 0.049, 67: 0.063, 87: 0.069, 100: 0.051), i.e. interior pixels are white about half the time – a lot of white, not thin strokes.

*Input weights W1 – which pixels matter.* Rows 0–11 (row 0), 12–14, 21–23, 24–25, 34–37, 47–48, 59–60, 71–72, 83–84, 95–96, 107–108, 119–120, 131–132, 143 are all ≈ ±0.02: row 0, col 0, col 11 and the ends of rows 1–3 are never looked at. Active pixels: row 1 only cols 3–8 (15: 0.112 u4; 17: 0.389 u19; 18: 0.173 u58; 20: 0.176 u14), row 2 cols 2–9, rows 3–11 cols 1–10. The active area is narrow at the top and widens downward – a rounded/peaked top over a flat full-width bottom.

*W1 units are oriented edge/line detectors along two diagonal directions and horizontal bands:*
- "/" lines running from the top centre down to the lower‑left: unit 4 (15: 0.112, 27: 0.314, 38: 0.258, 39: 0.382, 50: 0.405, 51: 0.295, 61: 0.238, 62: 0.331, 73: 0.202 → (1,3)…(6,1)); unit 5 (28: 0.338, 40: 0.329, 51: 0.416, 62: 0.345, 63: 0.327, 74: 0.398, 85: 0.261); unit 11 (42: 0.254, 54: 0.265, 65: 0.374, 76: 0.387, 88: 0.362, 99: 0.366, 111: 0.178 → (3,6)…(9,3)); unit 6 negative on the same kind of line (54: −0.401, 65: −0.413, 77: −0.488, 88: −0.416, 100: −0.401, 111: −0.328); units 36, 43, 54 likewise.
- "\" lines from top centre down to the lower‑right: unit 28 (30: 0.291, 31: 0.351, 42: 0.397, 43: 0.380, 55: 0.338, 56: 0.355), unit 14 (32: 0.396, 44: 0.391, 45: 0.323, 58: 0.421, 70: 0.209), unit 20 (57: 0.400, 68: 0.355, 69: 0.371, 81: 0.514, 93: 0.366, 94: 0.395), unit 46 (54: 0.353, 66: 0.379, 79: 0.275, 91: 0.306), unit 49 negative (55: −0.430, 79: −0.451, 92: −0.424, 104: −0.355, 117: −0.323), unit 59 (105: 0.460, 118: 0.454, 80: 0.305).
- Horizontal bands at the bottom: unit 38 on row 11 (133–142: 0.129 … 0.326 … 0.143), unit 8 on rows 9–10 (124–129: 0.377…0.483), unit 25 on rows 9–10 left (121: 0.371, 122: 0.561), unit 44 on row 9 (110–117: 0.235…0.361), unit 17 negative on row 10, units 12/13 negative on rows 8–9 (99–103 ≈ −0.30 … −0.36), unit 47 negative on rows 7–8 (86–92: −0.205 … −0.426), unit 31 negative on rows 9–10.
- Unit 2 contrasts a filled lower‑left block (87, 98–100, 110–112, 122–124 ≈ +0.16…+0.26) against the "\" line just right of it (64: −0.281, 77: −0.291, 89: −0.335, 102: −0.317, 114: −0.286): bright on one side of a slanted edge, dark on the other – a filled‑shape edge, not a stroke.

*Output weights W3 – what the hidden units paint.*
- h2 unit 13: a "/" line (1,5)=0.129, (2,4)=0.112, (3,4)=0.328, (4,3)=0.282, (5,3)=0.368, (6,2)=0.450, (7,2)=0.457, (8,1)=0.351 – from the top centre to the bottom‑left corner.
- h2 unit 21: a "\" line (1,7)=0.350, (2,8)=0.299, (3,8)=0.464, (4,9)=0.387, (5,9)=0.318, (6,10)=0.419, (7,10)=0.231, (8,11)=0.301; h2 unit 8: (2,6)=0.122, (3,7)=0.282, (4,7)=0.360, (5,8)=0.436, (6,8)=0.314, (7,9)=0.287, (8,10)=0.167; h2 unit 17: (3,6) … (7,8)=0.373, (8,8)=0.223, (9,9)=0.340, (10,10)=0.161, each with a dark fringe on its right (−0.367, −0.371, −0.441). Slopes everywhere ≈ 0.5–0.6 columns per row.
- Horizontal bottom edges: h2 unit 20 paints row 10 cols 1–10 (0.237 0.403 0.361 0.264 0.253 0.295 0.345 0.353 0.216 0.169); h2 unit 42 paints row 10 cols 2–7 (0.306 0.454 0.485 0.447 0.357) and faintly row 11; h2 unit 38 paints row 7 cols 1–10 (0.089 … 0.465 … 0.087) and darkens row 8; h2 unit 11 paints row 8 (0.115 0.180 0.273 0.218 0.146 0.173) and darkens row 9 (−0.36 … −0.42); h2 unit 12 paints row 6 (0.217 0.211 0.295 0.172 0.168) and darkens rows 7–9.
- Filled regions: h2 unit 35 paints a solid trapezoid, row 6 cols 3–8 (0.201 0.112 0.114 0.092), row 7 cols 2–9 (0.111 0.211 0.159 0.261 0.153 0.144), row 8 cols 1–9 (0.195 0.309 0.343 0.172 0.257 0.281 0.107 0.111), row 9 cols 2–9; h2 unit 36 fills rows 8–10 cols 2–9 (0.151 0.212 0.199 0.167 …; 0.248 0.455 0.371 0.327 0.259 0.172); h2 unit 29 fills a solid wedge rows 1–6, cols 1–5 (0.281, 0.307 0.517, 0.129 0.500 0.414 0.175, 0.343 0.440 0.291 0.253, 0.349 0.187 …) whose left boundary is a "/" line and darkens rows 6–10 cols 3–9.
- Several h2 units (24, 32, 41, 55, 57, 59) have unstructured ±0.1 noise rows – dead units.

**Reasoning**

The strokes the network detects and paints are of exactly three kinds: "/" lines going from the top centre down‑left, "\" lines going from the top centre down‑right (both with slope ≈ 0.55 col/row, i.e. ≈ 30° from vertical), and horizontal lines at the bottom (rows 6–11) spanning the width between those diagonals. Two diagonals meeting at a top apex plus a horizontal bottom edge is an upward‑pointing isosceles triangle; a half‑apex angle of ≈30° makes it close to equilateral (base ≈ height·1.1). The apex row/column and the base row vary from image to image (apexes seen at (1,3) … (1,8), bases painted at rows 6, 7, 8, 9, 10, 11), so size and position vary; the union of all apexes is what gives the rounded, narrowing top of the active region, and the unit that paints a "\" out to (8,11) shows the right corner can reach column 11 while row 0/col 0 stay black.

Filled rather than outlined: several output units paint solid regions (h2 35, 36, 29), the W1 unit 2 is a bright‑region‑vs‑dark‑edge contrast detector, the bottom‑edge painters simultaneously darken everything below the edge, and the interior output biases are ≈0 (half white on average) while the frame is ≈ −0.5 – thin outlines would leave the interior mean close to black.

Ruled out: digits like "2" (no right‑hand vertical hook, the "/" lines start at the top centre, not at the right), letter "A" (no crossbar unit, bottom edge is a full horizontal band), circles/rings (no curved left/right sides – all side detectors are straight diagonals of one fixed slope), downward triangles (no wide flat top edge: row 1 is only ever active at cols 3–8), and anything touching the border (row 0, col 0, col 11 are always black).

**Images**

Each generated image is a single solid white, upward‑pointing, roughly equilateral triangle on a black background: a point near the top (row 1–3, somewhere in columns 3–8), two straight sides sloping down at about 30° from vertical (≈ ½ column per row), and a flat horizontal base somewhere in rows 6–11 spanning up to columns 1–10. Size varies from about 5 to 9 rows high (base 5–10 pixels wide) and position shifts left/right and up/down, but the triangle never touches the outer one‑pixel frame. Interior is filled white, outside is black.

**Example**

```
............
.....##.....
.....##.....
....####....
....####....
...######...
...######...
..########..
..########..
.##########.
.##########.
............
```

**Confidence:** 55%
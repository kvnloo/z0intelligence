**Observations**

1. *Which pixels the network reads (W1 rows).* Rows 0–11 (image row 0), rows 132–143 (image row 11) and the pixels in columns 0 and 11 have only tiny weights (|w| ≲ 0.03, e.g. row 0: `0.001 0.006 -0.006 …`, row 12: `0.004 0.003 -0.012 …`, row 143: `-0.001 -0.004 0.000 …`). Large weights (|w| up to 0.4–0.5) start at pixel 14 (row 1, col 2: `-0.122 … 0.112`), run through pixels 15–21, 25/26–34, 37–46, 49–58, 61–70, 73–82, 85–94, 97–106, 109–118, 123–129, and stop. Corner pixels of that block (13, 22/23, 121/122, 130) are weak. So the content lives in rows 1–10, columns 1–10, with the corners of that block rarely used – a roughly rounded blob of ≈8–10 px in each direction, surrounded by an always-black 1-pixel frame.

2. *Time inputs.* Only the low-frequency step features matter: W1 rows 148–151 (sin, k=4..7) and 156–159 (cos, k=4..7) are huge (e.g. row 157: `0.563 -1.018 0.983 … 1.438 … -1.591 1.456`), while rows 144–147 and 152–155 are ≈0. Standard DDPM behaviour, nothing image-specific.

3. *Background.* b3 is negative everywhere on the frame (≈ −0.45 … −0.60 on rows 0, 11 and columns 0, 11) and rises to ≈0 to +0.18 in the centre (row 5: `… 0.011 0.081 -0.001 0.057 0.182 0.122 0.076 …`). Several h2 units with large positive b2 (unit 7: 0.680, units 11, 33, 34, 45: 0.44–0.57) have uniformly negative W3 rows (row 7 of W3: all ≈ −0.1…−0.2) – an "always-on black background" channel. So images are white strokes on black, the mean image being a grey blob in the middle: the contents vary a lot from image to image.

4. *Stroke templates in W3 (each row = what one hidden unit paints).* Reshaping the rows to 12×12:
   - unit 30: `0.194 0.408 0.507 0.503 0.323` at pixels 16–20 → a horizontal bar on row 1, cols 4–8; unit 4: `0.295 0.467 0.500 0.393` at 27–30 → a bar on row 2, cols 3–6 (tops of 7/5/3/2/0).
   - unit 42: 14–16 `0.351 0.552 0.382`, 25–26 `0.348 0.577`, 37 `0.539`, 49 `0.361`, 61 `0.219` → an arc from the top centre curving down the left edge (upper-left of 0/6/2/8).
   - unit 59: 31–33 `0.441 0.521`, 45 `0.578`, 58 `0.310`, 70 `0.185` → the mirror image, an upper-right arc.
   - unit 41: 91 `0.261`, 103 `0.327`, 116–117 `0.382 0.244`, 124–129 `0.244 0.419 0.525 0.582 0.367` plus 111–112 `0.232 0.229` → a bottom arc on rows 9–10 closing from the right side.
   - unit 18: 38, 50, 62, 74, 86 = `0.205 0.395 0.501 0.472 0.353` → straight vertical on column 2, rows 3–8 (left side of 0/6/4); unit 57 similar on cols 1–3; unit 2 (44, 56–57, 68–69: `0.385 0.427 0.312 0.220 0.201`) and unit 20 (58, 70, 82, 94: `0.360 0.519 0.519 0.344`) → verticals on cols 7–9 and col 10 (right sides).
   - unit 28: negatives `-0.208` (42), `-0.243` (53), `-0.141` (75), `-0.305` (98), `-0.296` (111), `-0.209` (123) → a diagonal from top-right (row 3, col 6–7) to bottom-left (row 10, col 3): the stroke of a 7 (or 2). Units 25, 29, 56 are "top bar + descending stroke" (unit 56: 29–33 `-0.287 -0.318 -0.258 -0.210`, then 40, 51, 63, 75, 87 `-0.333 -0.253 -0.420 -0.369 -0.305`), i.e. 7-shapes, each paired with positive weight in the lower-left (rows 6–10, cols 2–5) – "not a 7, but something with mass at lower left".
   - unit 37: 30–31 `-0.294 -0.303`, 42, 68, 80, 93, 105 `-0.295 -0.252 -0.252 -0.234 -0.146` → a slightly slanted vertical through the middle (a "1").
   - unit 3: row 5 cols 1–7 `-0.138 -0.215 -0.265 -0.241 -0.201 -0.230 -0.185`, row 6 `-0.206 -0.306 -0.337 -0.322 -0.233` → a horizontal crossbar at mid-height (3/4/5/8); unit 52: crossbar on row 5 (`-0.375 -0.445 -0.421`) plus a left stem on rows 6–10 and a positive bottom-centre blob.
   - unit 24 (88–89 `0.249 0.434`, 99–100 `0.252 0.196`), unit 26 (98–100 `0.222 0.458 0.333`), unit 46 (63, 75–76, 87–88 `0.245 0.286 0.287 0.251 0.237`) → lower loops / lower-left blobs (6, 8, 2, 5 bottoms); unit 40 → a filled centre (rows 5–7, cols 4–6) with a negative ring; unit 47 → a blob on rows 3–5, cols 4–6 (top of 1/4/9).
   - Units 14, 32, 55, 58(–ish) have unstructured ±0.1 noise in W3 (`0.020 -0.041 0.085 0.066 …`) – dead units left at initialisation.

**Reasoning**

The read/write region is a rounded ≈9×9 blob centred in the frame with an always-black border – exactly what a centred, size-normalised glyph gives (e.g. MNIST shrunk to 12×12). The templates are not object parts or textures but pen strokes: horizontal top bars, left and right arcs, straight left/right verticals, a bottom arc, a top-right-to-bottom-left diagonal, a central vertical, a mid-height crossbar, lower loops. Several units explicitly trade one stroke configuration for another (remove the 7-shape, add lower-left mass; remove the left arc; remove the central "1"), which only makes sense if the training set contains *several different* glyph classes drawn in roughly the same box. Uniform shapes (a single blob, a bar, a fixed object) are ruled out: the mean image (b3) is only mildly positive in the centre, and the templates are thin curved strokes, not filled regions. A single digit class is ruled out by the mutually exclusive templates (loops vs. 7-diagonal vs. central vertical vs. crossbar). The combination top bar + diagonal (7), closed-loop arcs (0/6/8/9), crossbar (3/4/5/8), central stroke (1), bottom-left hook plus base (2) covers the handwritten decimal digits.

**Images**

Low-resolution handwritten digits, 0–9 (MNIST-style), rendered as white (~+1) 1–2-pixel-wide strokes on a black (−1) background. Each digit is centred, occupying roughly rows 1–10 and columns 2–9 (about 8 px wide, 10 px tall), with the outermost row/column always black; the lower-right and lower-left corners of that box are used less. Different samples show different digits (loops like 0/6/8/9, a 7 with top bar and diagonal, a central vertical 1, digits with a mid crossbar such as 3/4/5, a 2 with a hooked bottom), with the usual handwriting variation in slant and thickness.

**Example**

```
............
....####....
...##..##...
..##....##..
..#......#..
..#......#..
..#......#..
..#......#..
..##....##..
...##..##...
....####....
............
```

**Confidence:** 62%
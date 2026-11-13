"""Check every path:line citation in the Z21 report without reading private state."""

from pathlib import Path
import re


root = Path(__file__).resolve().parents[2]
operation = root / "operations/z21-opus-infinite"
aliases = {
    "A": operation / "evidence/expected-audit.json",
    "S": operation / "evidence/current-z21/sources",
}
checked = 0
for bracket in re.findall(r"\[([^\[\]]+)\]", (operation / "REPORT.md").read_text()):
    for reference in bracket.split("; "):
        match = re.fullmatch(r"([^:\s]+):([0-9,\-]+)", reference)
        if not match:
            continue
        path, ranges = match.groups()
        if path == "A":
            target = aliases["A"]
        elif path.startswith("S/"):
            target = aliases["S"] / path[2:]
        else:
            target = root / path
        size = len(target.read_text().splitlines())
        for span in ranges.split(","):
            bounds = list(map(int, span.split("-")))
            assert 1 <= bounds[0] <= bounds[-1] <= size, (reference, size)
        checked += 1
assert checked >= 30, checked
print(f"PASS: {checked} report citations resolve to existing line ranges.")

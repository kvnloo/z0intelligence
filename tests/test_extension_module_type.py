"""An OMP extension that uses ESM syntax must declare its module type.

`omp-extensions/z0int-bridge/` had no `package.json` at all, while a sibling in
the same directory (`local-cognition`) declared `"type": "module"`. Seven
extensions, all written with `import`/`export`, none using `require` — and only
one declaring the module system it depended on.

That stayed invisible until it mattered: adding `import.meta.url` to
`z0int-bridge/index.ts` produced

    error TS1470: The 'import.meta' meta-property is not allowed in files which
    will build into CommonJS output

because with no `package.json` the compiler has to assume CommonJS. Three
extensions already relied on `import.meta` working; the dependency was implicit
in every case.

Declaring `"type": "module"` cannot break a file that is already written with
`import`/`export` — such a file is invalid as CommonJS, so the loader must
already be treating it as ESM. That is why this is a declaration of reality
rather than a behaviour change.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXTENSIONS = REPO / "omp-extensions"

ESM_SYNTAX = re.compile(r"^\s*(import\s|export\s)", re.MULTILINE)
CJS_SYNTAX = re.compile(r"\brequire\s*\(|\bmodule\.exports\b")


class ExtensionModuleTypeTests(unittest.TestCase):
    def _extensions(self) -> list[Path]:
        if not EXTENSIONS.is_dir():
            self.skipTest("no omp-extensions directory")
        return sorted(p for p in EXTENSIONS.iterdir() if p.is_dir())

    def test_every_esm_extension_declares_its_module_type(self) -> None:
        offenders: list[str] = []
        for ext in self._extensions():
            sources = list(ext.glob("*.ts"))
            if not sources:
                continue
            esm = any(ESM_SYNTAX.search(p.read_text(encoding="utf-8", errors="ignore"))
                      for p in sources)
            cjs = any(CJS_SYNTAX.search(p.read_text(encoding="utf-8", errors="ignore"))
                      for p in sources)
            if not esm or cjs:
                continue
            pj = ext / "package.json"
            if not pj.is_file():
                offenders.append(f"{ext.name}: no package.json")
                continue
            try:
                declared = json.loads(pj.read_text()).get("type")
            except (OSError, ValueError):
                offenders.append(f"{ext.name}: package.json is not valid JSON")
                continue
            if declared != "module":
                offenders.append(f"{ext.name}: package.json type={declared!r}")
        self.assertEqual(
            offenders,
            [],
            "these extensions are written with ESM syntax but do not declare it, "
            "so the module system is implicit and ESM-only syntax such as "
            "`import.meta` is a compile error:\n  " + "\n  ".join(offenders),
        )

    def test_z0int_bridge_derives_its_own_repo_root(self) -> None:
        """It must not name one machine's checkout."""
        index = EXTENSIONS / "z0int-bridge/index.ts"
        if not index.is_file():
            self.skipTest("z0int-bridge extension absent")
        text = index.read_text(encoding="utf-8")
        self.assertIn("import.meta.url", text)
        # the only permitted mention of an absolute checkout is inside a comment
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            self.assertNotIn(
                "/home/kvn/tmp/openjev", line,
                "z0int-bridge must derive its tree from its own module location, "
                f"not name a checkout: {line.strip()}",
            )


if __name__ == "__main__":
    unittest.main()

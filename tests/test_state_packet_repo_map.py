import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location('packet_repo_map', ROOT / 'scripts/state-packet/generate_packet.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTRY = """# registry
components:
  memory-mcp:
    name: Memory MCP
    repo: kvnloo/z0intelligence
    boundaries:
      owns: [read-only memory search, scoped grants]
      not_here: [new memory store]
  bare:
    name: no repo, no boundaries
  omp:
    repo: kvnloo/oh-my-pi
"""


class RepoMapRows(unittest.TestCase):
    def test_rows_are_sourced_and_carry_owns_and_not_here(self):
        m = load()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'components.yaml'
            p.write_text(REGISTRY)
            sources = {}
            rows = m.repo_map_rows(p, sources)
        self.assertEqual(len(rows), 2)
        self.assertIn(f'`{p}:3`', rows[0])
        self.assertIn('memory-mcp (kvnloo/z0intelligence): owns [read-only memory search, scoped grants]', rows[0])
        self.assertIn('not here [new memory store]', rows[0])
        self.assertIn('omp (kvnloo/oh-my-pi): owns []', rows[1])
        self.assertIn(str(p), sources)

    def test_missing_registry_is_empty_not_invented(self):
        m = load()
        self.assertEqual(m.repo_map_rows(Path('/nonexistent/components.yaml'), {}), [])


if __name__ == '__main__':
    unittest.main()

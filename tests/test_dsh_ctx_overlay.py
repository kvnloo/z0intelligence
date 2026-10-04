from pathlib import Path


def test_dsh_combined_overlay_keeps_ctx_on_pull_not_automatic_push():
    root = Path(__file__).resolve().parents[1]
    overlay = (root / "harness-adapters" / "dsh-z0intelligence" / "z0-dsh.cordis.yml").read_text()
    memory = (root / "harness-adapters" / "dsh-z0intelligence" / "memory.mjs").read_text()
    surface = (root / "src" / "z0int" / "memory" / "surface.py").read_text()

    assert "memory_inject: shadow" in overlay
    assert "z0-memory" in overlay
    assert "z0int.intelligence_mcp --profile memory" in overlay
    assert "z0int.memory.seam" in memory
    assert "LAYERS = ('temporal', 'lexical', 'semantic')" in surface
    assert "PULL_LAYERS = (*LAYERS, 'ctx')" in surface

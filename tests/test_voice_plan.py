from z0int.voice_plan import GpuSnapshot, build_brainstorm_plan


def test_brainstorm_plan_admits_12gb_gpu_with_free_vram():
    plan = build_brainstorm_plan(
        gpus=[GpuSnapshot(index=0, name="RTX 3080 Ti", total_vram_mb=12288, free_vram_mb=11800)]
    )
    assert plan["admission"]["status"] == "admitted"
    assert plan["admission"]["admitted"] is True
    assert plan["resource"]["residency"] == "session"
    assert plan["resource"]["idle_unload_seconds"] == 120
    assert plan["actor_id"] == "personaplex-7b-nf4"
    assert plan["device"]["name"] == "RTX 3080 Ti"


def test_brainstorm_plan_blocks_busy_gpu_without_eviction():
    plan = build_brainstorm_plan(
        gpus=[GpuSnapshot(index=0, name="RTX 3080 Ti", total_vram_mb=12288, free_vram_mb=7000)]
    )
    assert plan["admission"]["status"] == "blocked_busy"
    assert plan["admission"]["admitted"] is False
    assert plan["admission"]["reclaim_needed_mb"] == 3240


def test_brainstorm_plan_rejects_too_small_gpu():
    plan = build_brainstorm_plan(
        gpus=[GpuSnapshot(index=0, name="RTX 3070", total_vram_mb=8192, free_vram_mb=8192)]
    )
    assert plan["admission"]["status"] == "ineligible"
    assert plan["device"] is None


def test_plan_id_is_deterministic_for_same_host_snapshot():
    gpu = GpuSnapshot(index=0, name="RTX 3080 Ti", total_vram_mb=12288, free_vram_mb=11800)
    assert build_brainstorm_plan(gpus=[gpu])["plan_id"] == build_brainstorm_plan(gpus=[gpu])["plan_id"]

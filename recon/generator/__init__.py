"""recon.generator — synthetic world generator (SPEC §5).

`generate_world` is the single orchestration entrypoint `cli.py` and
`tests/gates/gate_p1.py` both call.
"""

from __future__ import annotations

import random

from recon.generator.defects import apply_all_defects
from recon.generator.truth import GroundTruth
from recon.generator.world import World, build_clean_world


def generate_world(seed: int, defects: bool = True) -> tuple[World, GroundTruth]:
    """Build the world for `seed`; apply the SPEC §5.3 defects unless disabled.

    All randomness flows through one `random.Random(seed)` created here
    (CLAUDE.md rule 3) — nothing else in the generator touches the global
    `random` module.
    """
    rng = random.Random(seed)
    world, truth = build_clean_world(rng)
    if defects:
        apply_all_defects(world, truth, rng)
    return world, truth

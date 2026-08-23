"""Treatment/control assignment.

Assignment happens **at intake** -- before diagnosis, before any policy sees the
case. Doing it later would let the engine's own behaviour influence who ends up
in which arm, which is the classic way a recovery experiment quietly reports its
own selection effect as a result.

The split is a deterministic hash of the case id, so it is reproducible across
runs and independent of iteration order.
"""

from __future__ import annotations

import hashlib

from razor_pay.schemas import Arm, RecoveryCase


def assign_arm(case_id: str, control_fraction: float, salt: str = "arm") -> Arm:
    digest = hashlib.sha256(f"{salt}:{case_id}".encode()).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return Arm.CONTROL if bucket < control_fraction else Arm.TREATMENT


def assign_all(
    cases: list[RecoveryCase], control_fraction: float = 0.3
) -> list[RecoveryCase]:
    for case in cases:
        case.arm = assign_arm(case.case_id, control_fraction)
    return cases

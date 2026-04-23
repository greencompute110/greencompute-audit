"""Compare validator-claimed weights against auditor's replay."""

from __future__ import annotations

# Tolerance for floating-point noise in scoring. Below this delta we accept
# the validator's number. Above, we flag the epoch.
TOLERANCE = 1e-4


def compare_weights(
    claimed: dict[str, float],
    replayed: dict[str, float],
    tolerance: float = TOLERANCE,
) -> dict[str, dict[str, float]]:
    """Return {hotkey: {claimed, replayed, delta}} for every hotkey whose
    claimed and replayed weights diverge by more than `tolerance`."""
    discrepancies: dict[str, dict[str, float]] = {}
    all_keys = set(claimed) | set(replayed)
    for hk in sorted(all_keys):
        c = claimed.get(hk, 0.0)
        r = replayed.get(hk, 0.0)
        delta = abs(c - r)
        if delta > tolerance:
            discrepancies[hk] = {"claimed": c, "replayed": r, "delta": delta}
    return discrepancies

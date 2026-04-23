"""Port of the validator's ScoreEngine — must match byte-for-byte.

Do NOT refactor this against the upstream. If the upstream changes formula,
this file must be updated in lockstep so auditors keep computing the same
numbers the validator claims to compute.

Source of truth: greenference-api/services/validator/src/greenference_validator/domain/scoring.py
"""

from __future__ import annotations

from math import sqrt
from statistics import median
from typing import Any


# Must mirror greenference_validator.config.settings — the validator publishes
# these in its config; auditors pin them here for deterministic replay.
SCORE_ALPHA = 1.0
SCORE_BETA = 1.3
SCORE_GAMMA = 1.1
SCORE_DELTA = 0.8
RENTAL_REVENUE_BONUS_CAP = 0.1


def _coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return sqrt(variance) / mean


def _consistency_penalty(results: list[dict]) -> float:
    successful = [r for r in results if r.get("success")]
    if len(successful) < 2:
        return 1.0
    latencies = [r["latency_ms"] for r in successful]
    throughputs = [r["throughput"] for r in successful]
    spread = max(
        _coefficient_of_variation(latencies),
        _coefficient_of_variation(throughputs),
    )
    if spread >= 0.6:
        return 0.7
    if spread >= 0.3:
        return 0.85
    return 1.0


def _fraud_penalty(results: list[dict]) -> float:
    if not results:
        return 0.0
    sigs = {r.get("benchmark_signature") for r in results if r.get("benchmark_signature")}
    signature_penalty = 0.75 if len(sigs) > 1 else 1.0
    proxy_penalty = 0.4 if any(r.get("proxy_suspected") for r in results) else 1.0
    readiness_penalty = max(0.2, 1.0 - (sum(r.get("readiness_failures", 0) for r in results) * 0.03))
    success_penalty = max(0.2, sum(1 for r in results if r.get("success")) / len(results))
    return round(
        signature_penalty
        * proxy_penalty
        * _consistency_penalty(results)
        * readiness_penalty
        * success_penalty,
        6,
    )


def _reliability_score(capability_reliability: float, results: list[dict]) -> float:
    if not results:
        return round(max(capability_reliability * 0.5, 0.01), 6)
    success_rate = sum(1 for r in results if r.get("success")) / len(results)
    readiness_penalty = max(0.2, 1.0 - (sum(r.get("readiness_failures", 0) for r in results) * 0.04))
    return round(max(capability_reliability * success_rate * readiness_penalty, 0.01), 6)


def _performance_score(capability_perf: float, results: list[dict]) -> float:
    if not results:
        return max(capability_perf * 0.5, 0.01)
    latencies = [r["latency_ms"] for r in results if r.get("success")]
    throughputs = [r["throughput"] for r in results if r.get("success")]
    if not latencies or not throughputs:
        return 0.01
    median_latency = median(latencies)
    median_throughput = median(throughputs)
    lat = min(1.0, 1000.0 / max(median_latency, 1.0))
    thr = min(2.0, median_throughput / 100.0)
    return round(max((lat * 0.5) + (thr * 0.5), 0.01), 6)


def replay_scoring(report_json: dict[str, Any]) -> dict[str, float]:
    """Given a validator audit report, recompute the final_score for each
    hotkey using ONLY the report's raw data (probes + scorecards). Returns
    {hotkey: final_score}. Auditors compare this against the validator's
    claimed weight_snapshot.weights.

    Note: The report's `scorecards` entries already contain the pre-computed
    fields (capacity_weight, reliability_score, etc). For a strict replay
    we recompute `final_score` from those fields using the published formula.
    A future stricter audit would recompute reliability/performance from the
    raw probe results too, but that requires knowing each miner's
    NodeCapability at the time of scoring — which the report could include
    in a future rev.
    """
    out: dict[str, float] = {}
    for sc in report_json.get("scorecards") or []:
        final = (
            sc["capacity_weight"]
            * (sc["security_score"] ** SCORE_ALPHA)
            * (sc["reliability_score"] ** SCORE_BETA)
            * (sc["performance_score"] ** SCORE_GAMMA)
            * sc["fraud_penalty"]
            * (sc["utilization_score"] ** SCORE_DELTA)
            * (1.0 + sc["rental_revenue_bonus"])
        )
        out[sc["hotkey"]] = round(final, 6)
    return out

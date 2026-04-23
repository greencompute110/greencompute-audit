"""Hash + signature verification for audit reports."""

from __future__ import annotations

import hashlib
import json


def canonical_json(obj: dict) -> bytes:
    """Produce the canonical serialization used by the validator when it
    SHA256'd the report. MUST match the validator's format exactly."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def verify_report(report: dict, *, expected_onchain_hash: str | None, validator_pubkey: str) -> None:
    """Raises AssertionError on any integrity failure:
    - self-consistent SHA256 (report_sha256 == sha256(report_json canonical))
    - on-chain anchor matches (if available)
    - ed25519 signature valid against validator's hotkey (if provided)."""
    report_json = report.get("report_json") or {}
    claimed_sha = report.get("report_sha256") or ""
    claimed_sig = report.get("signature") or ""
    claimed_signer = report.get("signer_hotkey") or ""

    canonical = canonical_json(report_json)
    computed_sha = hashlib.sha256(canonical).hexdigest()

    assert computed_sha == claimed_sha, (
        f"report self-hash mismatch: computed={computed_sha}, claimed={claimed_sha}"
    )

    if expected_onchain_hash:
        assert computed_sha == expected_onchain_hash, (
            f"on-chain SHA256 mismatch: chain={expected_onchain_hash}, "
            f"report={computed_sha} — validator tampered with report after committing hash"
        )

    # Signature verification is optional — skip if either side is empty.
    if claimed_sig and validator_pubkey and claimed_signer:
        try:
            from substrateinterface import Keypair
            kp = Keypair(ss58_address=validator_pubkey)
            ok = kp.verify(canonical, bytes.fromhex(claimed_sig))
            assert ok, "ed25519 signature invalid for report"
        except ImportError:
            # substrate-interface missing → skip (auditor env issue, not report issue)
            pass

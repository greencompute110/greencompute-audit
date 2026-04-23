"""CLI entry point for greencompute-audit."""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import click

from audit.chain import ChainClient
from audit.diff import compare_weights
from audit.fetch import ValidatorClient
from audit.replay import replay_scoring
from audit.verify import verify_report

logger = logging.getLogger("greencompute-audit")

EXIT_CLEAN = 0
EXIT_HASH_MISMATCH = 1
EXIT_MATH_DIVERGE = 2
EXIT_NETWORK = 3

STATE_FILE = Path(".audit_state")


def _read_last_audited_epoch() -> int | None:
    if not STATE_FILE.exists():
        return None
    try:
        return int(STATE_FILE.read_text().strip())
    except Exception:
        return None


def _write_last_audited_epoch(end_block: int) -> None:
    STATE_FILE.write_text(str(end_block))


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def audit_epoch(epoch_id: str, chain: ChainClient, api: ValidatorClient) -> int:
    """Audit a single epoch. Returns exit code for that epoch."""
    logger.info("auditing epoch %s", epoch_id)

    try:
        report = api.get_report(epoch_id)
    except Exception as exc:
        logger.error("epoch %s: failed to fetch report: %s", epoch_id, exc)
        return EXIT_NETWORK

    end_block = report["epoch_end_block"]
    try:
        on_chain_hash = chain.get_commitment_hash(end_block)
    except Exception as exc:
        logger.error("epoch %s: chain query failed: %s", epoch_id, exc)
        return EXIT_NETWORK

    # 1. hash + signature check
    try:
        verify_report(report, expected_onchain_hash=on_chain_hash, validator_pubkey=api.get_hotkey())
    except AssertionError as exc:
        logger.error("epoch %s: ✗ %s", epoch_id, exc)
        return EXIT_HASH_MISMATCH

    # 2. replay scoring math
    replayed = replay_scoring(report["report_json"])

    # 3. diff against validator's claimed weights
    claimed = (report["report_json"].get("weight_snapshot") or {}).get("weights") or {}
    discrepancies = compare_weights(claimed, replayed)
    if discrepancies:
        for hk, delta in discrepancies.items():
            logger.error(
                "epoch %s: ⚠ weight mismatch for %s — claimed=%.6f, replay=%.6f, Δ=%.6f",
                epoch_id, hk, delta["claimed"], delta["replayed"], delta["delta"],
            )
        return EXIT_MATH_DIVERGE

    logger.info(
        "epoch %s: ✓ hash matches on-chain, signature valid, weights replay-match (%d miners)",
        epoch_id, len(claimed),
    )
    return EXIT_CLEAN


def audit_new_epochs(chain: ChainClient, api: ValidatorClient) -> int:
    """Audit every unaudited epoch since last run. Returns worst exit code."""
    last_audited = _read_last_audited_epoch()
    reports = api.list_reports()
    worst = EXIT_CLEAN
    for r in sorted(reports, key=lambda x: x["epoch_end_block"]):
        end_block = r["epoch_end_block"]
        if last_audited is not None and end_block <= last_audited:
            continue
        code = audit_epoch(r["epoch_id"], chain, api)
        if code > worst:
            worst = code
        if code == EXIT_CLEAN:
            _write_last_audited_epoch(end_block)
    return worst


@click.command()
@click.option("--once", is_flag=True, help="Run one audit pass and exit")
@click.option("--loop", is_flag=True, help="Run continuously every AUDIT_INTERVAL_SECONDS")
@click.option("--epoch", type=str, default=None, help="Audit only this epoch_id and exit")
@click.option("-v", "--verbose", is_flag=True, help="Debug logging")
def main(once: bool, loop: bool, epoch: str | None, verbose: bool) -> None:
    _setup_logging(verbose)
    subtensor_url = os.environ.get("SUBTENSOR_URL", "wss://entrypoint-finney.opentensor.ai:443/")
    # GreenCompute netuid: 110 on mainnet (finney), 16 on testnet. Default to
    # mainnet since that's what most auditors want to watch; testnet is an
    # explicit NETUID=16 override.
    netuid = int(os.environ.get("NETUID", "110"))
    validator_endpoint = os.environ.get("VALIDATOR_ENDPOINT", "")
    interval = int(os.environ.get("AUDIT_INTERVAL_SECONDS", "300"))

    if not validator_endpoint:
        logger.error("VALIDATOR_ENDPOINT env var is required")
        sys.exit(2)

    chain = ChainClient(subtensor_url=subtensor_url, netuid=netuid)
    api = ValidatorClient(base_url=validator_endpoint)

    if epoch:
        sys.exit(audit_epoch(epoch, chain, api))

    if once:
        sys.exit(audit_new_epochs(chain, api))

    if loop:
        while True:
            try:
                audit_new_epochs(chain, api)
            except Exception:
                logger.exception("audit loop iteration failed")
            time.sleep(interval)

    # Neither flag → default to --once
    sys.exit(audit_new_epochs(chain, api))


if __name__ == "__main__":
    main()

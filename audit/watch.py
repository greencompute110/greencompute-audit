"""Watch weight-setting activity by every validator (vpermit=True) on a netuid.

Lists each permitted validator's:
  - UID, hotkey ss58
  - last_update block + how long ago it was
  - current weight vector size + top-3 targets
  - flags self-burn (weight concentrated on the validator's own UID)
  - flags stale (no weight update in >2h)

Default subnet is 110 (mainnet); pass --netuid 16 for testnet.

Usage from inside the audit container:
    docker compose run --rm --entrypoint python auditor -m audit.watch
    docker compose run --rm --entrypoint python auditor -m audit.watch --netuid 16
    docker compose run --rm --entrypoint python auditor -m audit.watch --watch

Or natively, after `pip install -e .`:
    python -m audit.watch
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import click
from substrateinterface import SubstrateInterface

BITTENSOR_BLOCK_SECS = 12.0  # rough wall-clock per block on Bittensor
STALE_THRESHOLD_BLOCKS = 600  # ~2h


def _fmt_age(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d{(secs % 86400) // 3600:02d}h"


@dataclass
class ValidatorRow:
    uid: int
    hotkey: str
    last_update_block: int
    blocks_ago: int
    weight_targets: list[tuple[int, int]]
    is_self_burn: bool
    top_targets: list[tuple[int, int]]


def collect_validator_state(
    s: SubstrateInterface, netuid: int
) -> tuple[int, list[ValidatorRow]]:
    current_block = s.get_block_number(None)

    permit_q = s.query("SubtensorModule", "ValidatorPermit", params=[netuid])
    permits: list[bool] = permit_q.value if permit_q else []

    last_q = s.query("SubtensorModule", "LastUpdate", params=[netuid])
    last_updates: list[int] = last_q.value if last_q else []

    # uid -> hotkey via Keys storage map
    uid_to_hotkey: dict[int, str] = {}
    for key_obj, hotkey_obj in s.query_map(
        module="SubtensorModule", storage_function="Keys", params=[netuid]
    ):
        try:
            uid_to_hotkey[int(key_obj.value)] = str(hotkey_obj.value)
        except (TypeError, ValueError, AttributeError):
            continue

    rows: list[ValidatorRow] = []
    for uid, has_permit in enumerate(permits):
        if not has_permit:
            continue
        hotkey = uid_to_hotkey.get(uid, "?")
        last_block = last_updates[uid] if uid < len(last_updates) else 0
        blocks_ago = max(0, current_block - last_block)

        w = s.query("SubtensorModule", "Weights", params=[netuid, uid])
        targets = [(int(t), int(v)) for t, v in (w.value or [])] if w else []

        is_self_burn = bool(targets) and all(t == uid for t, _ in targets)
        top_targets = sorted(targets, key=lambda tw: tw[1], reverse=True)[:3]

        rows.append(
            ValidatorRow(
                uid=uid,
                hotkey=hotkey,
                last_update_block=last_block,
                blocks_ago=blocks_ago,
                weight_targets=targets,
                is_self_burn=is_self_burn,
                top_targets=top_targets,
            )
        )

    rows.sort(key=lambda r: r.blocks_ago)
    return current_block, rows


def print_table(current_block: int, rows: list[ValidatorRow], netuid: int) -> None:
    print()
    print(f"netuid={netuid}    current_block={current_block}    permitted_validators={len(rows)}")
    print("-" * 116)
    print(
        f"{'UID':>5} {'Hotkey':<50} {'LastBlk':>10} {'Age':>10} {'Tgts':>5}  Top targets"
    )
    print("-" * 116)
    for r in rows:
        age_secs = r.blocks_ago * BITTENSOR_BLOCK_SECS
        age_str = _fmt_age(age_secs)
        marker = "* " if r.blocks_ago >= STALE_THRESHOLD_BLOCKS else "  "
        if r.is_self_burn:
            top_str = f"BURN -> uid={r.uid}"
        elif r.top_targets:
            top_str = ", ".join(f"uid={t}({w})" for t, w in r.top_targets)
        else:
            top_str = "(no weights set)"
        print(
            f"{marker}{r.uid:>3} {r.hotkey:<50} {r.last_update_block:>10} {age_str:>10} {len(r.weight_targets):>5}  {top_str}"
        )
    print("-" * 116)
    stale = sum(1 for r in rows if r.blocks_ago >= STALE_THRESHOLD_BLOCKS)
    burn = sum(1 for r in rows if r.is_self_burn)
    no_w = sum(1 for r in rows if not r.weight_targets)
    print(
        f"summary: {len(rows)} validators with permit  |  "
        f"{stale} stale (>2h)  |  {burn} self-burning  |  {no_w} no-weights"
    )
    print(f"legend: * = stale (last set_weights >2h ago)    BURN = all weight on own uid")


@click.command()
@click.option(
    "--subtensor",
    default=None,
    help="Subtensor RPC URL (default: $SUBTENSOR_URL or public archive)",
)
@click.option(
    "--netuid", default=110, type=int, show_default=True, help="Netuid (110 mainnet, 16 testnet)"
)
@click.option("--watch", is_flag=True, help="Refresh continuously every --interval seconds")
@click.option(
    "--interval",
    default=60,
    type=int,
    show_default=True,
    help="Refresh interval (seconds) when --watch is set",
)
def main(subtensor: str | None, netuid: int, watch: bool, interval: int) -> None:
    url = subtensor or os.environ.get(
        "SUBTENSOR_URL", "wss://archive.chain.opentensor.ai:443/"
    )
    s = SubstrateInterface(
        url=url,
        ss58_format=42,
        type_registry_preset="substrate-node-template",
        auto_reconnect=True,
    )
    try:
        while True:
            try:
                current_block, rows = collect_validator_state(s, netuid)
                print_table(current_block, rows, netuid)
            except Exception as exc:
                print(f"poll failed: {exc}", file=sys.stderr)
            if not watch:
                break
            time.sleep(interval)
    finally:
        try:
            s.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()

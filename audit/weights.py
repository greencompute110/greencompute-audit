"""Optional auditor-side weight setting.

When enabled, this module publishes a `set_weights` extrinsic to the chain
using the auditor's OWN validator hotkey (must be registered on the netuid
with sufficient stake to receive a validator permit). The weights are
derived from independently replaying the validator's audit reports — so a
dishonest validator's weight vector is shadowed by an independent one
computed from the same publicly verifiable raw data.

Disabled by default. Read-only verification (`SET_WEIGHTS_ENABLED=false`)
is the primary mode and requires no keys whatsoever.

How keys are handled (security note for skeptical operators):
- The auditor's `AUDITOR_HOTKEY_SECRET_SEED` is read from env at runtime
  and used locally to sign extrinsics with `substrate-interface`. The seed
  is NEVER transmitted anywhere — same threat model as Chutes' `audit.py`.
- The seed corresponds to a hotkey YOU control. The validator being
  audited never sees it, neither does the chain (only the resulting
  signature, which is what set_weights expects).
- Don't commit `.env` to git. Treat the seed file like a wallet.
"""

from __future__ import annotations

import logging
import os

from substrateinterface import Keypair, SubstrateInterface

logger = logging.getLogger(__name__)

# `version_key` is a chain-side compatibility marker. 0 is accepted on most
# subnets; some require a non-zero value matching the runtime version.
# Override via AUDITOR_WEIGHTS_VERSION_KEY if needed.
DEFAULT_VERSION_KEY = 0


def is_enabled() -> bool:
    return os.environ.get("SET_WEIGHTS_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _load_keypair() -> Keypair | None:
    """Load the auditor's signing keypair from env.

    Two ways to pass the seed:
      AUDITOR_HOTKEY_SECRET_SEED=0x<hex>
      AUDITOR_HOTKEY_SECRET_SEED=<bare hex without 0x>

    Returns None if not configured — caller should treat as "weights not
    set, audit-only mode".
    """
    seed = os.environ.get("AUDITOR_HOTKEY_SECRET_SEED", "").strip()
    if not seed:
        return None
    if seed.startswith("0x"):
        seed = seed[2:]
    try:
        return Keypair.create_from_seed(seed)
    except Exception:
        logger.exception("failed to construct keypair from AUDITOR_HOTKEY_SECRET_SEED")
        return None


def _normalize_to_u16(weights: list[float]) -> list[int]:
    """Normalize float weights to chain-format u16 (0..65535) with sum-to-max."""
    total = sum(weights) or 1.0
    return [int((w / total) * 65535) for w in weights]


def submit_weights(
    subtensor_url: str,
    netuid: int,
    weights_by_hotkey: dict[str, float],
) -> bool:
    """Submit a set_weights extrinsic on `netuid` with the given hotkey -> weight map.

    Returns True on success (extrinsic accepted into a block), False otherwise.

    The auditor's own ss58 (derived from AUDITOR_HOTKEY_SECRET_SEED) must be
    registered on the netuid; otherwise the chain rejects with NotRegistered.
    """
    keypair = _load_keypair()
    if keypair is None:
        logger.warning(
            "set_weights enabled but AUDITOR_HOTKEY_SECRET_SEED not set — skipping"
        )
        return False

    auditor_ss58 = keypair.ss58_address
    version_key = int(os.environ.get("AUDITOR_WEIGHTS_VERSION_KEY", str(DEFAULT_VERSION_KEY)))

    try:
        substrate = SubstrateInterface(
            url=subtensor_url,
            ss58_format=42,
            type_registry_preset="substrate-node-template",
            auto_reconnect=True,
        )
    except Exception:
        logger.exception("failed to connect to subtensor at %s", subtensor_url)
        return False

    try:
        # Confirm we are registered as a validator on this netuid.
        my_uid_q = substrate.query("SubtensorModule", "Uids", params=[netuid, auditor_ss58])
        my_uid = my_uid_q.value if my_uid_q else None
        if my_uid is None:
            logger.warning(
                "auditor hotkey %s not registered on netuid=%d — cannot set weights "
                "(register with `btcli subnet register --netuid %d` first)",
                auditor_ss58,
                netuid,
                netuid,
            )
            return False

        # Map every audited hotkey to a UID; drop ones not in the metagraph.
        uids: list[int] = []
        normalized_weights: list[float] = []
        for hotkey, weight in sorted(weights_by_hotkey.items()):
            uid_q = substrate.query("SubtensorModule", "Uids", params=[netuid, hotkey])
            uid = uid_q.value if uid_q else None
            if uid is None:
                logger.info("hotkey %s not in metagraph; skipping for weight set", hotkey)
                continue
            uids.append(uid)
            normalized_weights.append(weight)

        if not uids:
            logger.warning("no audited hotkeys mapped to UIDs; nothing to publish")
            return False

        weights_u16 = _normalize_to_u16(normalized_weights)
        logger.info(
            "auditor (uid=%d, ss58=%s) submitting set_weights for %d UIDs on netuid=%d",
            my_uid,
            auditor_ss58,
            len(uids),
            netuid,
        )

        call = substrate.compose_call(
            call_module="SubtensorModule",
            call_function="set_weights",
            call_params={
                "netuid": netuid,
                "dests": uids,
                "weights": weights_u16,
                "version_key": version_key,
            },
        )
        extrinsic = substrate.create_signed_extrinsic(call=call, keypair=keypair)
        receipt = substrate.submit_extrinsic(extrinsic, wait_for_inclusion=True)

        tx_hash = (
            receipt.extrinsic_hash if hasattr(receipt, "extrinsic_hash") else str(receipt)
        )
        logger.info("auditor set_weights tx submitted: %s (uids=%d)", tx_hash, len(uids))
        return True
    except Exception:
        logger.exception("auditor set_weights extrinsic failed")
        return False
    finally:
        try:
            substrate.close()
        except Exception:
            pass

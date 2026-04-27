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
- Wallets live on disk at `~/.bittensor/wallets/<coldkey>/hotkeys/<hotkey>`
  (standard Bittensor path). The auditor's docker-compose mounts that
  directory READ-ONLY into the container at `/root/.bittensor/wallets`.
- You pass the wallet IDENTIFIERS (coldkey/hotkey NAMES) via env vars,
  not the secret material itself. The audit code reads the matching
  wallet JSON file at runtime and constructs a Keypair locally.
- Nothing is transmitted off-box. The validator being audited never sees
  your wallet; no third party sees it.
- Don't commit `.env` to git. The wallet files themselves stay outside
  the repo, on the operator's host filesystem.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

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


def _wallet_root() -> Path:
    """Where wallets live inside the container.

    Default `/root/.bittensor/wallets` matches the docker-compose bind mount
    of the host's `~/.bittensor`. Override with AUDITOR_WALLET_PATH if you
    have a non-standard layout.
    """
    return Path(
        os.environ.get("AUDITOR_WALLET_PATH", "/root/.bittensor/wallets")
    )


def _load_keypair() -> Keypair | None:
    """Load the auditor's signing keypair from a Bittensor wallet file.

    Reads `~/.bittensor/wallets/<coldkey>/hotkeys/<hotkey>` (path adjustable
    via AUDITOR_WALLET_PATH). The wallet JSON has a `secretSeed` field —
    we hand that to `Keypair.create_from_seed()`.

    `Keypair.create_from_uri(path)` is wrong here — it treats the string
    as an Sr25519 derivation URI like `//Alice` and produces a fake
    deterministic keypair. Use `create_from_seed` after reading the file.

    Returns None if AUDITOR_COLDKEY_NAME isn't set — caller treats as
    "weights not set, read-only mode".
    """
    coldkey = os.environ.get("AUDITOR_COLDKEY_NAME", "").strip()
    hotkey = os.environ.get("AUDITOR_HOTKEY_NAME", "default").strip()
    if not coldkey:
        return None

    wallet_file = _wallet_root() / coldkey / "hotkeys" / hotkey
    if not wallet_file.is_file():
        logger.error(
            "wallet file not found: %s (mount your ~/.bittensor read-only into "
            "the container; see docker-compose.yml + README)",
            wallet_file,
        )
        return None

    try:
        with wallet_file.open() as f:
            data = json.load(f)
        seed = (
            data.get("secretSeed")
            or data.get("privateKey")
            or data.get("seed")
            or data.get("private_key")
        )
        if not seed:
            logger.error("no secretSeed/privateKey field in %s", wallet_file)
            return None
        if isinstance(seed, str) and seed.startswith("0x"):
            seed = seed[2:]
        return Keypair.create_from_seed(seed)
    except Exception:
        logger.exception("failed to load keypair from %s", wallet_file)
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
            "set_weights enabled but AUDITOR_COLDKEY_NAME / AUDITOR_HOTKEY_NAME "
            "not set or wallet file unreadable — skipping"
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

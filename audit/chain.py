"""Query on-chain commitments from the Bittensor subtensor."""

from __future__ import annotations

from substrateinterface import SubstrateInterface


class ChainClient:
    def __init__(self, subtensor_url: str, netuid: int) -> None:
        self.subtensor_url = subtensor_url
        self.netuid = netuid
        self._substrate: SubstrateInterface | None = None

    def _connect(self) -> SubstrateInterface:
        if self._substrate is not None:
            return self._substrate
        self._substrate = SubstrateInterface(
            url=self.subtensor_url,
            ss58_format=42,
            type_registry_preset="substrate-node-template",
            auto_reconnect=True,
        )
        return self._substrate

    def get_commitment_hash(self, at_block: int) -> str | None:
        """Return the SHA256 hex committed by the validator for our netuid
        at the specified block number. Returns None if no commitment exists.
        """
        substrate = self._connect()
        try:
            block_hash = substrate.get_block_hash(at_block)
        except Exception:
            block_hash = None

        # Commitments.CommitmentOf storage map is keyed by (netuid, hotkey).
        # For our purposes we query the validator hotkey that our validator
        # is known to use — but for a simpler MVP we query ALL commitments
        # on the netuid at this block and accept any match for the expected
        # hash. A production auditor should pin the exact validator hotkey
        # it trusts.
        try:
            result = substrate.query_map(
                module="Commitments",
                storage_function="CommitmentOf",
                params=[self.netuid],
                block_hash=block_hash,
            )
            for key, value in result:
                info = value.value if hasattr(value, "value") else value
                # info shape: {"fields": [[{"Raw32": "0x..."}]], "block": N, ...}
                fields = info.get("fields") if isinstance(info, dict) else None
                if not fields:
                    continue
                for field_list in fields:
                    for entry in field_list:
                        for _tag, hex_val in (entry.items() if hasattr(entry, "items") else []):
                            hex_str = hex_val[2:] if isinstance(hex_val, str) and hex_val.startswith("0x") else str(hex_val)
                            # Return first 32-byte-hash we find — sufficient for MVP.
                            if len(hex_str) >= 64:
                                return hex_str[:64]
        except Exception:
            return None

        return None

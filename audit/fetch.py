"""HTTP client for the validator's public audit endpoints."""

from __future__ import annotations

import httpx


class ValidatorClient:
    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def list_reports(self, limit: int = 500) -> list[dict]:
        r = self._client.get(f"{self.base_url}/validator/v1/audit/reports", params={"limit": limit})
        r.raise_for_status()
        return r.json().get("reports", [])

    def get_report(self, epoch_id: str) -> dict:
        r = self._client.get(f"{self.base_url}/validator/v1/audit/reports/{epoch_id}")
        r.raise_for_status()
        return r.json()

    def get_hotkey(self) -> str:
        r = self._client.get(f"{self.base_url}/validator/v1/audit/hotkey.pub")
        r.raise_for_status()
        return r.json().get("ss58_address", "")

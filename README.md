# greenference-audit

Independent verifier for **Green Compute** (Bittensor subnet 16).

Any validator, miner, or observer can run this to check that the subnet's
owner validator is scoring miners honestly and submitting truthful weights
to the chain. No GPU required — this is a pure-Python replay of the
validator's scoring math over publicly-published audit reports, cross-checked
against on-chain SHA256 commitments.

## What this does

For each Bittensor epoch (every 360 blocks ≈ 72 min on netuid 16):

1. Queries the bittensor chain for the `Commitments.CommitmentOf(16, <epoch_end_block>)` hash committed by the validator.
2. Downloads the full audit report JSON from the validator's public endpoint.
3. Recomputes `SHA256(canonical_json(report))` and asserts it matches the on-chain hash. **Tampering caught here.**
4. Verifies the ed25519 signature on the report against the validator's published hotkey pubkey.
5. Replays the `ScoreEngine` formula on the report's raw probe data to re-derive each miner's final score.
6. Compares the replay output against the `weight_snapshot.weights` the validator claims it used → flags discrepancies.
7. Exits 0 (clean) / 1 (hash mismatch) / 2 (math diverges).

If math diverges across multiple epochs, you have on-chain evidence of validator misbehavior. In a future release the auditor will be able to submit its own weight extrinsic to override dishonest validators.

## Hardware

- CPU: 2+ cores
- RAM: 2 GB
- Disk: 10 GB (audit reports are ~50 KB each)
- **No GPU** — no inference re-run

## Install

```bash
git clone https://github.com/greenference/greenference-audit.git
cd greenference-audit
cp .env.example .env     # edit to point at your subtensor + validator
docker compose up -d
```

Or natively:

```bash
pip install -e .
python -m audit --help
```

## Config

`.env`:

```
SUBTENSOR_URL=wss://entrypoint-finney.opentensor.ai:443/   # or your local subtensor
NETUID=16
VALIDATOR_ENDPOINT=https://validator.green-compute.com
AUDIT_INTERVAL_SECONDS=300                                  # how often to poll for new epochs
```

## Usage

One-shot (checks all unaudited epochs then exits):

```bash
python -m audit --once
```

Long-running loop (polls every `AUDIT_INTERVAL_SECONDS`):

```bash
python -m audit --loop
```

Check a specific epoch:

```bash
python -m audit --epoch 16-1234560
```

## Exit codes

- `0` — all audited epochs pass
- `1` — at least one hash mismatch (tamper detected — investigate immediately)
- `2` — at least one math divergence (scoring formula produced different weights from what was committed on-chain)
- `3` — network / RPC error (transient, retry later)

## Understanding the output

```
epoch 16-1234560: ✓ hash matches on-chain, signature valid, weights replay-match (37 miners)
epoch 16-1234920: ✗ HASH MISMATCH — on-chain SHA256=abc... report SHA256=def...
epoch 16-1235280: ⚠ math diverge — validator claims 0.234 for 5F6WRq..., replay says 0.198 (Δ=0.036)
```

## License

Apache 2.0 — independent verification is a public good.

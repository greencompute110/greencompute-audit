# greenference-audit

Independent verifier for **Green Compute** — Bittensor subnet **110 on mainnet**, **16 on testnet**.

Any validator, miner, or observer can run this to check that the subnet's
owner validator is scoring miners honestly and submitting truthful weights
to the chain. No GPU required — this is a pure-Python replay of the
validator's scoring math over publicly-published audit reports, cross-checked
against on-chain SHA256 commitments.

## What this does

For each Bittensor epoch (every 360 blocks ≈ 72 min, same tempo on both netuids):

1. Queries the bittensor chain for the `Commitments.CommitmentOf(NETUID, <epoch_end_block>)` hash committed by the validator. NETUID = 110 on mainnet (default), 16 on testnet.
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
# Mainnet (default):
SUBTENSOR_URL=wss://entrypoint-finney.opentensor.ai:443/
NETUID=110

# Testnet (override):
# SUBTENSOR_URL=wss://test.finney.opentensor.ai:443/
# NETUID=16

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
python -m audit --epoch 110-1234560   # mainnet epoch at block 1234560
python -m audit --epoch 16-1234560    # testnet
```

## Exit codes

- `0` — all audited epochs pass
- `1` — at least one hash mismatch (tamper detected — investigate immediately)
- `2` — at least one math divergence (scoring formula produced different weights from what was committed on-chain)
- `3` — network / RPC error (transient, retry later)

## Understanding the output

```
epoch 110-1234560: ✓ hash matches on-chain, signature valid, weights replay-match (37 miners)
epoch 110-1234920: ✗ HASH MISMATCH — on-chain SHA256=abc... report SHA256=def...
epoch 110-1235280: ⚠ math diverge — validator claims 0.234 for 5F6WRq..., replay says 0.198 (Δ=0.036)
```

## How this fits in the subnet

```
validator (owner)                           auditor (you)
  |                                              |
  |-- every ~72 min (360 blocks):                |
  |   1. compute scorecards                      |
  |   2. publish WeightSnapshot                  |
  |   3. set_weights() on Bittensor              |
  |   4. generate AuditReport                    |
  |   5. sha256(canonical_json)                  |
  |   6. set_commitment(netuid, hash)  ←--- on chain ---→ auditor reads hash
  |   7. sign + expose at /audit/reports/{epoch}    |
  |                                              |-- fetch report over HTTP
  +- (next epoch)                                |-- verify sha256 == on-chain
                                                 |-- verify ed25519 signature
                                                 |-- replay ScoreEngine formula
                                                 +- diff vs claimed weights
```

If anything diverges, the auditor flags it. Since the validator committed the hash on-chain **before** publishing the report, they can't silently alter the report after the fact. They can only lie by either (a) committing a hash that doesn't match the raw data — catchable by replay — or (b) committing the hash of a report whose weights diverge from `ScoreEngine(probes, scorecards)` — also catchable.

## Source-of-truth sync

`audit/replay.py` is a deliberate **port** of the validator's `ScoreEngine` from [greenference-api](https://github.com/greenference/greenference-api) at:

`services/validator/src/greenference_validator/domain/scoring.py`

When the validator changes its scoring formula, this file must be updated in lockstep. The scoring constants (`SCORE_ALPHA`, `SCORE_BETA`, ...) are pinned here — if the validator changes them without shipping a coordinated auditor update, every epoch will diverge and this tool will flag it (which is the correct behavior — unilateral formula changes are suspicious).

## Architecture

Full subnet docs in [../README.md](../README.md).

## License

Apache 2.0 — independent verification is a public good.

# greencompute-audit

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
git clone https://github.com/greencompute110/greencompute-audit.git
cd greencompute-audit
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
# Mainnet (default) — archive endpoint (required, see note below):
SUBTENSOR_URL=wss://archive.chain.opentensor.ai:443/
NETUID=110

# Testnet (override):
# SUBTENSOR_URL=wss://test.finney.opentensor.ai:443/
# NETUID=16

VALIDATOR_ENDPOINT=https://validator.green-compute.com
AUDIT_INTERVAL_SECONDS=300                                  # how often to poll for new epochs
```

### Why an archive endpoint (not a lite node)

Each epoch, the validator calls `set_commitment(netuid, sha256)` which **overwrites** the previous commitment in the chain's `Commitments.CommitmentOf` map. To verify a past report you have to query the commitment **at the historical block** the validator posted it, which is a state-at-block query. Lite nodes prune state after ~256 blocks (~30 min) and can't answer those queries — miss a single polling window with a lite node and you lose the ability to verify any commitment from the gap.

Two ways to get archive access:

1. **Public archive** (recommended): `wss://archive.chain.opentensor.ai:443/` — run by the Opentensor Foundation, free, no setup. This is what the default `.env.example` points to.
2. **Self-hosted archive**: ~1 TB disk (and growing), continuous sync bandwidth, full node uptime. Only worth it if you don't want to trust the public endpoint or need higher rate limits.

The auditor itself is CPU-only (hashing + Python scoring replay). You do NOT need 1 TB of disk on the auditor machine — the archive node can be remote. A 2 vCPU / 4 GB RAM / 40 GB disk VPS is enough (Hetzner CX22, Oracle Always-Free, etc.).

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

## Optional: independent weight setting (Chutes-style)

By default this auditor is **purely read-only** — it verifies SHA256 anchors, signatures, and replays scoring math. No keys are needed, nothing is transmitted, nothing is signed. Many auditors will run it in this mode forever.

For validators who want to push their **own** weight vector to the chain based on independently replayed audit data — same pattern Chutes uses on subnet 64 — flip `SET_WEIGHTS_ENABLED=true` and provide your validator hotkey's seed. The auditor then publishes a `set_weights` extrinsic each cycle using the replayed scores. This shadows a dishonest validator's weight vector with one derived from publicly verifiable raw data.

### Requirements when enabling weight setting

1. Your hotkey is **registered as a validator on the netuid** (mainnet 110 or testnet 16):
   ```bash
   btcli subnet register --netuid 110 --wallet.name <coldkey> --wallet.hotkey <hotkey> --subtensor.network finney
   ```
2. Your hotkey has enough stake to receive a validator permit. Check with `btcli subnet hyperparameters --netuid 110 --subtensor.network finney`.
3. Your `~/.bittensor/wallets/<coldkey>/hotkeys/<hotkey>` file exists on the host (it does if you've used `btcli` to create it).

### How keys are handled (security)

**Nothing is transmitted off your box.** The pattern matches `btcli` itself:

- Wallet files live on your **host** at `~/.bittensor/wallets/...` (standard Bittensor path).
- The auditor's `docker-compose.yml` mounts that directory into the container as **read-only** (`/root/.bittensor/wallets:ro`).
- Inside the container, the audit code reads `secretSeed` from the wallet JSON and builds a `Keypair` locally with `substrate-interface`.
- `set_weights` extrinsics are signed locally and submitted to the chain RPC. The chain validates the signature against the public hotkey already in its metagraph.
- The validator being audited never sees your wallet. No third party sees it.
- You only paste **wallet identifiers** (coldkey/hotkey names) into `.env` — no secret material in env vars.

### Config snippet

```bash
# Add to your auditor's .env
SET_WEIGHTS_ENABLED=true
AUDITOR_COLDKEY_NAME=my-validator       # the directory name under ~/.bittensor/wallets/
AUDITOR_HOTKEY_NAME=default             # the file name under .../hotkeys/
# BITTENSOR_WALLET_DIR=~/.bittensor     # uncomment only if your wallets aren't at the default
# AUDITOR_WEIGHTS_VERSION_KEY=0         # rarely needed; override only if your subnet requires non-zero
```

`docker compose up -d` reads `BITTENSOR_WALLET_DIR` from `.env` (defaults to `~/.bittensor`) and bind-mounts `<that-dir>/wallets` into the container as read-only.

After enabling, every audit cycle that produces at least one verified epoch will trigger a follow-up extrinsic. Look for `auditor set_weights tx submitted: 0x...` in the logs to confirm.

### When to leave it disabled

- You don't have a validator registration on the netuid (most operators)
- You only want to detect cheating, not counter-set weights
- You're running the auditor as a public good / observatory

In any of those cases, the default `SET_WEIGHTS_ENABLED=false` does the right thing.

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

`audit/replay.py` is a deliberate **port** of the validator's `ScoreEngine` from [greencompute-api](https://github.com/greencompute110/greencompute-api) at:

`services/validator/src/greencompute_validator/domain/scoring.py`

When the validator changes its scoring formula, this file must be updated in lockstep. The scoring constants (`SCORE_ALPHA`, `SCORE_BETA`, ...) are pinned here — if the validator changes them without shipping a coordinated auditor update, every epoch will diverge and this tool will flag it (which is the correct behavior — unilateral formula changes are suspicious).

## Architecture

Full subnet docs in [../README.md](../README.md).

## License

Apache 2.0 — independent verification is a public good.

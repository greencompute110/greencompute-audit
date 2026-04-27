# greencompute-audit

Independent verifier for **Green Compute** — Bittensor subnet **110 on mainnet**, **16 on testnet**.

Any validator, miner, or observer can run this to check that the subnet's owner validator is scoring miners honestly and submitting truthful weights to the chain. No GPU required — pure-Python replay of the validator's scoring math over publicly-published audit reports, cross-checked against on-chain SHA256 commitments.

## TL;DR for validators considering running this

- **Default mode is read-only**, no keys required, no risk. Verify what the owner validator is doing.
- **Optional weight-setting mode** (Chutes-style): if you're a registered validator on netuid 110, the auditor can publish its own `set_weights` extrinsic per cycle from independently replayed scoring data — keeping a dishonest owner validator in check.
- **Wallet handling matches `btcli`**: you reference your wallet by `coldkey/hotkey` name; the wallet directory is bind-mounted **read-only** into the container; secrets never leave your host or appear in env vars.
- **Set-and-forget**: `docker compose up -d` once → runs forever. No PM2/systemd/cron required.
- **Hardware**: 8-16 CPU cores, 64 GB RAM, 1 TB disk, good bandwidth. CPU only — no GPU.

## What this does

For each Bittensor epoch (every 360 blocks ≈ 72 min, same tempo on both netuids):

1. Queries the bittensor chain for the `Commitments.CommitmentOf(NETUID, <epoch_end_block>)` hash committed by the validator. NETUID = 110 on mainnet (default), 16 on testnet.
2. Downloads the full audit report JSON from the validator's public endpoint.
3. Recomputes `SHA256(canonical_json(report))` and asserts it matches the on-chain hash. **Tampering caught here.**
4. Verifies the ed25519 signature on the report against the validator's published hotkey pubkey.
5. Replays the `ScoreEngine` formula on the report's raw probe data to re-derive each miner's final score.
6. Compares the replay output against the `weight_snapshot.weights` the validator claims it used → flags discrepancies.
7. Exits 0 (clean) / 1 (hash mismatch) / 2 (math diverges).

If math diverges across multiple epochs, you have on-chain evidence of validator misbehavior. With `SET_WEIGHTS_ENABLED=true`, the auditor will also submit its own `set_weights` extrinsic each cycle, replacing the dishonest validator's vector with one derived from publicly-verifiable raw data.

## Install + run

The auditor is built to be **set-and-forget**. `docker compose up -d` once and it stays alive across crashes and host reboots — no PM2, no systemd unit, no cron required.

### Quickstart (read-only audit, the default)

```bash
git clone https://github.com/greencompute110/greencompute-audit.git
cd greencompute-audit
cp .env.example .env             # edit if you need non-default settings
docker compose up -d auditor     # builds image on first run, ~30-60s
docker compose logs -f auditor   # watch the audit cycles
```

That's the whole setup. The container runs `python -m audit --loop`, which polls every `AUDIT_INTERVAL_SECONDS` (default 300s = 5 min) and verifies any new epochs. `restart: unless-stopped` keeps it alive forever.

### Update to a newer release

```bash
cd greencompute-audit
git pull
docker compose build auditor
docker compose up -d --force-recreate auditor
```

### Native install (without docker)

If you'd rather not use docker:

```bash
pip install -e .
python -m audit --once    # one-shot
python -m audit --loop    # continuous
```

You'd want to wrap this in `systemd`, `pm2`, or a `screen/tmux` session for it to survive reboots. Docker compose handles that for you.

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

The auditor itself is CPU-only (hashing + Python scoring replay). The 1 TB disk + 64 GB RAM target is sized to also host your own archive node alongside the audit container, so you don't depend on a third party's public endpoint for the chain queries the audit relies on.

## Operations

### Run modes

| Command | Behavior |
|---|---|
| `docker compose up -d auditor` | Default: long-running loop, polls every `AUDIT_INTERVAL_SECONDS` |
| `docker compose run --rm auditor --once` | One-shot: audit all unaudited epochs since last run, exit |
| `docker compose run --rm auditor --epoch 110-8060653` | Audit one specific epoch_id and exit |
| `docker compose run --rm auditor --once -v` | Verbose / debug logging |
| `docker compose logs -f auditor` | Tail logs of the long-running container |

The same flags work natively if you skipped docker: `python -m audit --once`, etc.

### State + idempotency

The auditor remembers the last successfully audited block in `audit_state/last_audited_epoch` (mounted volume). On restart it picks up where it left off — no double-checking, no missed epochs as long as the container runs at least once before the chain prunes (~256 blocks, ~30 min).

### Hardware spec

- **CPU**: 8-16 cores
- **RAM**: 64 GB
- **Disk**: 1 TB SSD (sized to co-host an archive subtensor node alongside the auditor)
- **Bandwidth**: stable, low-latency — the auditor talks to subtensor over WebSocket continuously
- **No GPU** — auditor is pure-Python hash + score replay

The auditor process itself is light (~50 MB RAM, <1% of a core). The big-box spec is so the same machine can run a full archive subtensor node, eliminating any dependency on third-party RPC endpoints for the verification path.

### Auto-updating (optional)

If you want the auditor to keep itself current with upstream changes, run a periodic `git pull && docker compose build && docker compose up -d --force-recreate` via:

- **cron**: easiest, no extra deps. Daily check is plenty:
  ```
  0 4 * * *  cd /opt/greencompute-audit && git pull && docker compose build auditor && docker compose up -d --force-recreate auditor
  ```
- **systemd timer**: same idea, more visibility into history (`systemctl status`, `journalctl -u`).
- **PM2** (Chutes-style): if you're already using PM2 for other services, wrap the same shell command. PM2 isn't needed just for this — `restart: unless-stopped` already handles process supervision.

Skip this if you'd rather upgrade on your own cadence.

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

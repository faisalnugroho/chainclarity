# ChainClarity

Understand any blockchain transaction in plain English — with evidence.

Paste a transaction hash, pick a network (Ethereum or Solana mainnet), optionally
ask a question about the transaction, and get:

1. A **plain-English explanation** built from provider-reported facts (status,
   sender, recipient, value, fee, timestamp) — with explicit caveats.
2. A **structured evidence table** showing exactly which fields the explorer API
   reported, when it was retrieved, and what is missing.
3. An **on-chain adjudication** of your question by a GenLayer Intelligent
   Contract: validators independently re-evaluate whether the evidence supports
   your claim and reach consensus on a verdict of `SUPPORTED`,
   `CONTRADICTED`, or `INSUFFICIENT_EVIDENCE`.

## Why this is different from "ask an AI about a tx"

- The interpretation step runs **on-chain under GenLayer consensus**, not inside
  one opaque API call. Validators re-run the same bounded evaluation; the verdict
  stored on-chain is the consensus outcome, and every claim + adjudication has a
  retrievable on-chain record.
- The contract **derives nothing from the leader's word alone**: deterministic
  gates validate the evidence schema before any model sees it, and every LLM
  failure mode maps to a well-formed `INSUFFICIENT_EVIDENCE` (fail-safe).
- Explorer data is always labeled **provider-reported**, never presented as
  verified on-chain truth, and a *successful* transaction is never described as
  "safe".

## Differentiation from the owner's other projects

| Project | Focus | ChainClarity |
|---|---|---|
| MilestoneJudge | escrow release for project milestones | no escrow/money; transaction comprehension |
| TokenScope | token contract forensics/security flags | plain-language tx explanation for end users |
| RainHedge | parametric weather-cover escrow | no insurance semantics |
| RecallShield / ToolGuard / GenDid / RRO | product-recall / tool-call / identity / release adjudication | transaction evidence adjudication |
| TrustReconciler | wallet risk reconciliation (address-level) | transaction-level comprehension; wallet-address analysis explicitly out of scope in v1 |

No contract code, storage schema, prompt structure, or product workflow is
reused from those repositories.

## Architecture

```
Browser (frontend/index.html, GitHub Pages)
   │  POST /api/analyze {network, target}
   ▼
ChainClarity API (api/server.py + api/adapters.py, stdlib only)
   │  Etherscan v2 (chainid=1)      Solscan Pro v2
   │  keys: ETHERSCAN_API_KEY / SOLSCAN_API_KEY — env vars, server-side only
   ▼  bounded evidence document
Browser → genlayer-sdk bundle → GenLayer Studionet RPC
   │  submit_claim(network, tx_hash, claim, evidence)
   │  adjudicate(claim_id)  ← nondeterministic evaluation + validator consensus
   ▼  get_claim(claim_id)  — public, retrievable record
```

- **Deterministic work** (validation, schema checks, hashing, unit formatting)
  stays in ordinary code — the API layer and the contract's submit/gate paths.
- **Nondeterministic work** (does this evidence actually support this claim?) is
  the only part that runs under consensus, via `gl.vm.run_nondet` with a custom
  validator that compares the verdict label only (reasoning may differ).
- The contract never fetches URLs itself in v1; it evaluates the bounded
  evidence document the app supplies — documented limitation.

## Supported networks (v1)

| Network | Provider | Transaction hash | Wallet address |
|---|---|---|---|
| Ethereum mainnet | Etherscan v2 (`chainid=1`) | ✅ | ❌ not supported yet |
| Solana mainnet | Solscan Pro v2 | ✅ | ❌ not supported yet |

Wallet-address history endpoints cannot provide the bounded, reliable evidence
the contract requires on the configured API plans; rather than fabricate
results, address analysis is explicitly disabled (the API returns
`address_not_supported`, the UI says so).

## Live deployment (Studionet, chain 61999)

| Item | Value |
|---|---|
| Contract | `0x3996E1832B2d7a8Ec10310DE596B93cB93CD7e21` |
| Deploy tx | `0x1600e4f3fd5a0809068b9a21e04d5b3a36f8ac804ca0e0bd5666fcc7646af37c` |
| Explorer | https://explorer-studio.genlayer.com/contracts/0x3996E1832B2d7a8Ec10310DE596B93cB93CD7e21 |
| Frontend | https://faisalnugroho.github.io/chainclarity/ |
| Evidence log | `docs/deployment_log.json` |

## Running locally

```bash
# API server
python3 -m venv venv && . venv/bin/activate
cp .env.example .env         # fill ETHERSCAN_API_KEY / SOLSCAN_API_KEY
pip install -r requirements-dev.txt
set -a; . ./.env; set +a
python api/server.py         # listens on :8080

# Frontend: serve frontend/ statically; set localStorage.cc_api_base
# to the API origin if it is not same-origin.

# Contract tests (direct mode, no Docker)
python -m pytest tests/direct -q

# Full suite (adapters, integration, secrets)
python -m pytest tests -q

# Lint the contract
genvm-lint check contracts/chainclarity.py
```

Deploy + smoke: `python scripts/deploy_studionet.py` (writes
`docs/deployment_log.json`; `--smoke-only <addr>` re-runs smoke on an existing
deployment).

## Security notes

- API keys are read from environment variables **only**, on the server. They
  never appear in the browser bundle, responses, logs, or Git history
  (enforced by `tests/test_secrets.py`).
- All provider/user input is validated; responses are size-bounded; requests
  are timed out; per-IP rate limiting on `/api/analyze`; short-TTL cache.
- HTTP 200 is never treated as transaction success — status comes from the
  provider's own transaction-status field.
- The API never logs request bodies (targets may be sensitive).
- Provider-reported metadata is treated as untrusted data; the contract's
  prompt labels it as DATA and the deterministic pipeline clamps field sizes
  (prompt-injection resistance is tested: `test_prompt_injection_in_metadata_not_authoritative`).
- No wallet connection, no signatures, no private keys required for analysis.
  (The in-browser burner wallet exists only to sign the testnet adjudication
  transactions; it holds no real value and stays in your browser.)

## Limitations

- Explorer data can be delayed, rate-limited, or wrong; evidence is
  provider-reported, not independently re-verified on-chain by this app.
- A `success` status means the transaction executed — **not** that it was safe,
  honest, or what you intended. The UI states this on every result.
- Native ETH/SOL transfers and fees only; token transfers are not decoded in v1.
- Wallet-address analysis is not supported yet.
- The contract evaluates evidence as submitted; it does not fetch explorers
  itself in v1.
- The adjudication LLM consensus is nondeterministic by design; verdicts are
  stable decisions (label-level), but reasoning text may differ between runs.
- Deployed on GenLayer **Studionet** (testnet); not audited, not for
  production use.

## Tests

75 automated tests, all runnable offline with mocks:

- `tests/direct` (31) — contract behavior on a real GenVM: input validation,
  all three verdicts, fail-safe on malformed/unknown LLM output, idempotent
  re-adjudication, prompt-injection, state transitions, record retrieval.
- `tests/test_adapters.py` (26) — Etherscan/Solscan adapters: success, failed,
  pending, unknown, not-found, quota, timeout, 429, malformed, caching,
  decimal formatting, no-key leakage in errors.
- `tests/test_integration.py` (12) — frontend↔API↔contract source-to-source
  pins, live HTTP roundtrip against a mocked provider, error taxonomy.
- `tests/test_secrets.py` (6) — no key literals in frontend/backend/bundle,
  no private-key patterns anywhere in the repo.

Mocks are used for determinism; **mocks are not proof of live integration** —
live evidence is in `docs/deployment_log.json` (deploy tx + per-scenario tx
hashes on Studionet).

## License

MIT

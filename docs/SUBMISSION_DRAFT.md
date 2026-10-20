# ChainClarity — Submission Draft

Category: **Project** · Repo: https://github.com/faisalnugroho/chainclarity ·
Live: https://faisalnugroho.github.io/chainclarity/

> STATUS: DRAFT — do not submit until Phase E audit passes and the owner
> approves.

## Portal fields (draft)

**Title** (≤80 chars):
ChainClarity — plain-English transaction explanations with on-chain adjudication

**Description** (≤1000 chars) — 4 drafts, pick/trim at submit time:

D1 (966 chars):
"Ordinary users paste a transaction hash and can't tell what actually happened.
ChainClarity fetches public explorer evidence (Etherscan for Ethereum, Solscan
Pro for Solana), explains the transaction in plain English, and — when the user
asks a question like 'did this send 1 ETH to my exchange?' — adjudicates the
claim on GenLayer. A Python Intelligent Contract validates the bounded evidence
schema deterministically, then validators independently re-evaluate whether the
evidence supports the claim and reach consensus on SUPPORTED /
CONTRADICTED / INSUFFICIENT_EVIDENCE. Deterministic gates make every LLM
failure mode fail-safe; the verdict plus reasoning is stored on-chain and
retrievable by anyone. Explorer data is labeled provider-reported, never
'verified truth'; a successful transaction is never called safe. Deployed on
Studionet (contract 0x3996E1832B2d7a8Ec10310DE596B93cB93CD7e21); live dApp on
GitHub Pages; 75 automated tests; secrets server-side only."

**Expected verification outcome** (≤500 chars):
"Reviewer pastes an Ethereum or Solana tx hash, gets a plain-English
explanation with an evidence table, and can ask a question that is adjudicated
on-chain by contract 0x3996E1832B2d7a8Ec10310DE596B93cB93CD7e21 on Studionet —
verdict SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE with tx hashes
recorded in docs/deployment_log.json. Reproduce locally: pytest tests →
python api/server.py. Mocks power offline tests; live evidence is the
deployment log + explorer links."

## Evidence table (fill after smoke completes)

| Scenario | Claim | Result | tx |
|---|---|---|---|
| Supported-expected | "transferred 1.5 ETH…" | TBD | TBD |
| Contradicted-expected | "sent 100 ETH to burn address" | TBD | TBD |
| Garbage evidence | fail-safe path | TBD | TBD |

## Why GenLayer consensus is genuinely needed here

Whether a free-text claim is entailed by structured evidence is a judgment
call a Solidity contract cannot make; a single centralized AI API would be an
unaccountable authority over that judgment. GenLayer validators re-run the
evaluation and consensus decides — the stored verdict is not one provider's
opinion.

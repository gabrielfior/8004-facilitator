# x402 × ERC-8004 Reputation Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ERC-8004 reputation signals to x402 via the `reputation` extension, enabling agents to build on-chain reputation from payment interactions.

**Architecture:**
- **Agent Server (resource server)**: One EOA per agent. EOA address IS the agent identity. Signs x402 PAYMENT-RESPONSE with its EOA key at `interactionHash = keccak256("x402:reputation:v1" || taskRef || dataHash)` — provides cryptographic proof-of-interaction to clients.
- **Facilitator (OpenMID)**: Validates agent signatures and proof-of-interaction before accepting feedback submissions. Acts as gatekeeper for reputation system integrity.
- **Client (payer)**: Pays for service via x402, receives signed PAYMENT-RESPONSE, optionally submits feedback with proof-of-interaction to the facilitator.

**Decisions:**
- Signatures mandatory (protects reputation system integrity)
- Feedback submission optional (no mandatory UX burden)
- One EOA per agent (simplest model, no NFT registry)
- $1 attack vector handled via dedup + optional facilitator attestation

**Repos involved:**
- `./x402-sdk/` — x402 Python SDK fork (locally cloned from x402-foundation/x402)
- `./` — OpenMID facilitator (this repo)

**Key Decisions:**
| Decision | Choice | Rationale |
|---|---|---|
| **Agent identity** | One EOA per agent | Simplest model — no NFT registry, no registration file. EOA address IS the agent identity. |
| **Response signing** | Mandatory | Protects reputation system integrity. Without cryptographic proof, any feedback is untrustworthy. |
| **Feedback submission** | Optional | Agents that don't participate simply don't submit feedback. No mandatory UX burden. |
| **Client SDK** | Python (httpx client + FastAPI server) | Pragmatic choice — Python SDK already exists with httpx transport and FastAPI middleware patterns. No JS/Go needed for initial implementation. |
| **HTTP transport** | httpx only | Narrow scope — the existing Python x402 SDK already uses httpx for async HTTP. No requests/urllib3/aiohttp variants in scope. |
| **Server framework** | FastAPI only | The Python x402 SDK provides a FastAPI middleware (`http/middleware/fastapi_middleware.py`). No Flask/Starlette/Django variants in scope. |
| **Facilitator** | OpenMID (this repo) | Existing OpenMID facilitator (TypeScript/Express) handles verify, settle, register, and reputation endpoints. We extend it with proof-of-interaction validation and dedup. |
| **Dedup strategy** | Redis (runtime) + Subgraph (bootstrap) | Redis provides fast per-facilitator dedup at runtime. Subgraph indexes the `NewFeedback` event's `feedbackHash` — bootstraps Redis from history and enables cross-facilitator dedup without contract changes. Falls back to Redis-only if no subgraph URL configured. |
| **No contract changes** | Core principle | All ERC-8004 ReputationRegistry contracts stay untouched. The `feedbackHash` parameter on `giveFeedback` already exists — we use it. |

---

## Extensions in x402 — Feasibility Check

Q: Are extensions already allowed on x402? How much refactoring is needed?

**Yes, extensions are already a first-class concept.** No breaking changes needed:

- `PaymentRequired.extensions: dict[str, Any] | None` — server declares enabled extensions
- `PaymentPayload.extensions: dict[str, Any] | None` — client responds with extension data
- The existing `extensions/` directory has working examples: `bazaar`, `payment_identifier`, `eip2612_gas_sponsoring`, `erc20_approval_gas_sponsoring`
- The facilitator (JS) already registers `erc-8004` extension via `registerExtension("erc-8004")`
- The Python/JS agent examples already use `extensions: { "erc-8004": { ... } }` in their config

**What's needed:** A new `extensions/reputation/` package following the same pattern as existing extensions. No core SDK changes required — the new extension hooks into existing lifecycle hooks (`onAfterSettle`, `onBeforeRespond`).

---

## Task 1: Python SDK — `reputation` Extension Types

**Files** (in `./x402-sdk/python/x402/`):
- Create: `extensions/reputation/__init__.py`
- Create: `extensions/reputation/types.py`
- Create: `extensions/reputation/signing.py`
- Modify: `extensions/__init__.py`

- [ ] **Step 1a: Create `extensions/reputation/__init__.py`**

```python
from x402.extensions.reputation.types import ProofOfInteraction
from x402.extensions.reputation.signing import (
    compute_data_hash,
    compute_interaction_hash,
    sign_interaction_hash,
    verify_agent_signature,
)

__all__ = [
    "ProofOfInteraction",
    "compute_data_hash",
    "compute_interaction_hash",
    "sign_interaction_hash",
    "verify_agent_signature",
]
```

- [ ] **Step 1b: Create `extensions/reputation/types.py`**

```python
from pydantic import BaseModel


class ProofOfInteraction(BaseModel):
    """Cryptographic proof linking a payment to a specific service interaction.

    Fields:
        taskRef: CAIP-220 payment reference ("eip155:8453/0x...")
        dataHash: keccak256(request_bytes || response_bytes)
        agentAddress: the agent's EOA address
        agentSignature: ECDSA signature over interactionHash
    """
    taskRef: str
    dataHash: str
    agentAddress: str
    agentSignature: str
```

- [ ] **Step 1c: Create `extensions/reputation/signing.py`**

Uses `Web3.keccak()` — returns `HexBytes` (a `bytes` subclass), not a plain `str`:

```python
from web3 import Web3
from eth_account.messages import encode_defunct
from eth_account import Account
from hexbytes import HexBytes


DOMAIN_SEPARATOR = "x402:reputation:v1"


def compute_data_hash(request_body: bytes, response_body: bytes) -> HexBytes:
    """Compute keccak256(request || response)."""
    return Web3.keccak(request_body + response_body)


def compute_interaction_hash(task_ref: str, data_hash: HexBytes) -> HexBytes:
    """Compute keccak256("x402:reputation:v1" || taskRef || dataHash)."""
    payload = DOMAIN_SEPARATOR + task_ref + data_hash.hex()
    return Web3.keccak(text=payload)


def sign_interaction_hash(
    private_key: str,
    task_ref: str,
    data_hash: HexBytes,
) -> HexBytes:
    """Sign interaction hash with the agent's EOA private key.

    Returns the raw 65-byte signature as HexBytes.
    """
    interaction_hash = compute_interaction_hash(task_ref, data_hash)
    message = encode_defunct(hexstr=interaction_hash.hex())
    signed = Account.sign_message(message, private_key)
    return HexBytes(signed.signature)


def verify_agent_signature(
    interaction_hash: HexBytes,
    signature: HexBytes,
    expected_address: str,
) -> bool:
    """Recover the signer address from a signature and compare it to the expected agent address."""
    message = encode_defunct(hexstr=interaction_hash.hex())
    recovered = Account.recover_message(message, signature=signature)
    return recovered.lower() == expected_address.lower()
```

- [ ] **Step 1d: Update `extensions/__init__.py`**

```python
from x402.extensions import reputation
```

### Unit tests

```python
# tests/extensions/reputation/test_signing.py
from x402.extensions.reputation.signing import (
    compute_data_hash,
    compute_interaction_hash,
    verify_agent_signature,
)
from eth_account import Account
from hexbytes import HexBytes


def test_compute_data_hash_deterministic():
    req = b'{"model":"gpt-4"}'
    res = b'{"choices":[]}'
    h1 = compute_data_hash(req, res)
    h2 = compute_data_hash(req, res)
    assert h1 == h2
    assert isinstance(h1, HexBytes)


def test_sign_and_verify():
    account = Account.create()
    task_ref = "eip155:8453/0xabc123"
    data_hash = compute_data_hash(b"req", b"res")
    interaction_hash = compute_interaction_hash(task_ref, data_hash)

    signature = sign_interaction_hash(account.key.hex(), task_ref, data_hash)
    assert verify_agent_signature(interaction_hash, signature, account.address)
```

---

## Task 2: Python SDK — Integrate `reputation` Extension into Client (httpx only)

**Files** (in `./x402-sdk/python/x402/`):
- Modify: `client_base.py`
- Modify: `http/clients/httpx_client.py` (the httpx-specific transport)

- [ ] **Step 2a: Client passes reputation extension data through**

In `client_base.py`, when building `PaymentPayload`, check if server declared `reputation` in `PaymentRequired.extensions`. If yes, include client's identity (optional):

```python
# In create_payment_payload or similar:
if payment_required.extensions and "reputation" in payment_required.extensions:
    # Client can opt in to declaring its identity
    if self._reputation_enabled and self._signer_address:
        ext_data = payload.extensions or {}
        ext_data["reputation"] = {"clientAddress": self._signer_address}
        payload.extensions = ext_data
```

- [ ] **Step 2b: Surface signature data from PAYMENT-RESPONSE in httpx client**

In `http/clients/httpx_client.py`, after receiving a successful payment response, check the PAYMENT-RESPONSE header for `agentSignature` and `dataHash`. Store them on the response object:

```python
# In the round-trip handler:
if payment_response and payment_response.extensions:
    rep_ext = payment_response.extensions.get("reputation", {})
    if rep_ext.get("agentSignature"):
        # Make available to the caller
        context.proof_of_interaction = ProofOfInteraction(
            taskRef=payment_response.transaction,
            dataHash=rep_ext["dataHash"],
            agentAddress=rep_ext["agentAddress"],
            agentSignature=rep_ext["agentSignature"],
        )
```

---

## Task 3: Python SDK — Agent-Side Response Signing (FastAPI only)

**Files** (in `./x402-sdk/python/x402/`):
- Create: `extensions/reputation/server.py`
- Modify: `http/middleware/fastapi_middleware.py` (the FastAPI-specific middleware)

- [ ] **Step 3a: Create `extensions/reputation/server.py`**

```python
from x402.extensions.reputation.signing import compute_data_hash, sign_interaction_hash
from x402.schemas.payments import PaymentRequired


def enrich_response_with_reputation(
    payment_required: PaymentRequired,
    response: dict,
    request_body: bytes,
    response_body: bytes,
    task_ref: str,
    agent_private_key: str,
) -> dict:
    """Sign the response and attach reputation data.

    Returns the response dict enriched with reputation extension fields.
    """
    from eth_account import Account
    data_hash = compute_data_hash(request_body, response_body)
    signature = sign_interaction_hash(agent_private_key, task_ref, data_hash)
    agent_address = Account.from_key(agent_private_key).address

    response["extensions"] = response.get("extensions") or {}
    response["extensions"]["reputation"] = {
        "agentSignature": signature.hex(),
        "dataHash": data_hash.hex(),
        "agentAddress": agent_address,
    }
    return response
```

- [ ] **Step 3b: Integrate into FastAPI middleware**

In `http/middleware/fastapi_middleware.py`, after payment settlement, if the resource config has a `reputation` extension enabled and an agent private key, sign the response before returning it:

```python
# In the middleware response handler:
reputation_cfg = resource.get("extensions", {}).get("reputation", {})
if reputation_cfg and agent_private_key:
    from x402.extensions.reputation.server import enrich_response_with_reputation
    response_data = enrich_response_with_reputation(
        payment_required=payment_required,
        response=response_data,
        request_body=request_body,
        response_body=response_body,
        task_ref=task_ref,
        agent_private_key=agent_private_key,
    )
```

---

## Task 4: Facilitator — Proof-of-Interaction Validation

**Files** (in `./`):
- Create: `src/services/proofOfInteraction.ts`
- Modify: `index.ts` (update `/feedback` endpoint)

- [ ] **Step 4a: Create `src/services/proofOfInteraction.ts`**

```typescript
import { keccak256, type Hex } from "viem";

const DOMAIN_SEPARATOR = "x402:reputation:v1";

export type ProofOfInteraction = {
  taskRef: string;
  dataHash: Hex;
  agentAddress: `0x${string}`;
  agentSignature: Hex;
};

export function computeDataHash(
  requestBody: Uint8Array,
  responseBody: Uint8Array,
): Hex {
  const combined = new Uint8Array(requestBody.length + responseBody.length);
  combined.set(requestBody);
  combined.set(responseBody, requestBody.length);
  return keccak256(combined);
}

export function computeInteractionHash(
  taskRef: string,
  dataHash: Hex,
): Hex {
  const payload = `${DOMAIN_SEPARATOR}${taskRef}${dataHash}`;
  return keccak256(new TextEncoder().encode(payload));
}

export function verifyAgentSignature(
  proof: ProofOfInteraction,
): boolean {
  const interactionHash = computeInteractionHash(proof.taskRef, proof.dataHash);
  const { recoverMessageAddress } = require("viem");
  const recovered = recoverMessageAddress({
    message: { raw: interactionHash },
    signature: proof.agentSignature,
  });
  return recovered.toLowerCase() === proof.agentAddress.toLowerCase();
}
```

- [ ] **Step 4b: Write unit tests**

```typescript
import { describe, it, expect } from "vitest";
import { computeDataHash, computeInteractionHash } from "../src/services/proofOfInteraction";

describe("proofOfInteraction", () => {
  it("computes deterministic dataHash", () => {
    const req = new TextEncoder().encode("request");
    const res = new TextEncoder().encode("response");
    const hash = computeDataHash(req, res);
    expect(hash).toMatch(/^0x[0-9a-f]{64}$/);
    expect(computeDataHash(req, res)).toBe(hash);
  });
});
```

- [ ] **Step 4c: Update `/feedback` endpoint in `index.ts`**

Add `proofOfInteraction` as an optional field to POST /feedback. When provided, validate the agent signature before processing:

```typescript
if (proofOfInteraction) {
  const isValid = verifyAgentSignature(proofOfInteraction);
  if (!isValid) {
    return res.status(400).json({
      success: false,
      error: "Invalid proof-of-interaction: agent signature mismatch",
    });
  }
}
```

---



## Task 5: Facilitator — Agent Identity in Payment Flow

**Files** (in `./`):
- Modify: `index.ts`

- [ ] **Step 5a: Parse reputation extension from PaymentPayload**

In the `onAfterSettle` hook, extract the agent's EOA address from the `reputation` extension if present:

```typescript
const reputationInfo = extensions?.["reputation"] as
  | { agentAddress?: string }
  | undefined;

if (reputationInfo?.agentAddress) {
  console.log(`Reputation identity: ${reputationInfo.agentAddress}`);
}
```

- [ ] **Step 5b: Add `GET /reputation/verify` endpoint**

A public endpoint for clients to verify an agent's signature on a past interaction:

```
GET /reputation/verify?taskRef=eip155:8453/0x...&dataHash=0x...&signature=0x...&agentAddress=0x...

200: { valid: true, recoveredAddress: "0x..." }
```

---

## Task 6: Deduplication — Redis + Subgraph Bootstrapping

**Approach:** Redis (primary runtime check) + Subgraph (bootstrap from history + cross-facilitator authority).

The `NewFeedback` event emitted by `giveFeedback()` includes `feedbackHash` as a `bytes32` field — a subgraph indexes all these events. On facilitator startup, we query the subgraph to pre-populate Redis with every `feedbackHash` ever submitted, bootstrapping from history across ALL facilitators.

**Files** (in `./`):
- Create: `src/services/dedupStore.ts`
- Create: `subgraph/subgraph.yaml`
- Create: `subgraph/schema.graphql`
- Create: `subgraph/src/mapping.ts`
- Modify: `index.ts`
- Modify: `src/config/env.ts` (add `SUBGRAPH_URL`)

- [ ] **Step 6a: Create `src/services/dedupStore.ts`**

```typescript
import { createRedisStore } from "./redisStore";
import type { Hex } from "viem";

export function createDedupStore(redisUrl: string, subgraphUrl?: string) {
  const store = createRedisStore<string>(redisUrl);

  async function bootstrapFromSubgraph(): Promise<number> {
    if (!subgraphUrl) return 0;
    let loaded = 0;
    let skip = 0;
    const pageSize = 1000;

    while (true) {
      const query = `{
        feedbacks(first: ${pageSize}, skip: ${skip}) { feedbackHash }
      }`;
      const res = await fetch(subgraphUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const json = await res.json();
      const feedbacks: { feedbackHash: string }[] = json.data?.feedbacks || [];
      if (feedbacks.length === 0) break;

      for (const fb of feedbacks) {
        await store.set(fb.feedbackHash.toLowerCase(), "1");
        loaded++;
      }
      skip += feedbacks.length;
    }
    return loaded;
  }

  return {
    check: async (hash: Hex): Promise<boolean> => {
      const existing = await store.get(hash.toLowerCase());
      return existing !== null;
    },
    mark: async (hash: Hex): Promise<void> => {
      await store.set(hash.toLowerCase(), "1");
    },
    bootstrapFromSubgraph,
  };
}
```

- [ ] **Step 6b: Create subgraph**

**`subgraph/schema.graphql`:**
```graphql
type Feedback @entity {
  id: ID!
  agentId: BigInt!
  clientAddress: Bytes!
  feedbackIndex: BigInt!
  feedbackHash: Bytes!
  timestamp: BigInt!
}
```

**`subgraph/src/mapping.ts`:**
```typescript
import { NewFeedback } from "../generated/ReputationRegistry/ReputationRegistry";
import { Feedback } from "../generated/schema";

export function handleNewFeedback(event: NewFeedback): void {
  const id = `${event.params.agentId}-${event.params.clientAddress.toHex()}-${event.params.feedbackIndex}`;
  const feedback = new Feedback(id);
  feedback.agentId = event.params.agentId;
  feedback.clientAddress = event.params.clientAddress;
  feedback.feedbackIndex = event.params.feedbackIndex;
  feedback.feedbackHash = event.params.feedbackHash;
  feedback.timestamp = event.block.timestamp;
  feedback.save();
}
```

**`subgraph/subgraph.yaml`:**
```yaml
specVersion: 0.0.5
schema:
  file: ./schema.graphql
dataSources:
  - kind: ethereum
    name: ReputationRegistry
    network: base
    source:
      address: "0x8004B663056A597Dffe9eCcC1965A193B7388713"
      abi: ReputationRegistry
      startBlock: 0
    mapping:
      kind: ethereum/events
      apiVersion: 0.0.7
      language: wasm/assemblyscript
      entities:
        - Feedback
      abis:
        - name: ReputationRegistry
          file: ./abis/ReputationRegistry.json
      eventHandlers:
        - event: NewFeedback(uint256,address,uint64,int128,uint8,string,string,string,string,string,bytes32)
          handler: handleNewFeedback
      file: ./src/mapping.ts
```

- [ ] **Step 6c: Integrate dedup into `/feedback` endpoint**

```typescript
import { createDedupStore } from "./src/services/dedupStore";

const dedupStore = createDedupStore(REDIS_URL, process.env.SUBGRAPH_URL);

// Bootstrap on startup
dedupStore.bootstrapFromSubgraph().then(n => {
  console.log(`Bootstrapped ${n} existing feedback hashes from subgraph`);
});

// In POST /feedback handler:
const interactionHash = computeInteractionHash(proof.taskRef, proof.dataHash);
const isDuplicate = await dedupStore.check(interactionHash);
if (isDuplicate) {
  return res.status(409).json({
    success: false,
    error: "Duplicate proof: feedback already submitted for this interaction",
  });
}
// ... process feedback ...
await dedupStore.mark(interactionHash);
```

- [ ] **Step 6d: Add `SUBGRAPH_URL` to env config**

```typescript
// src/config/env.ts
export const SUBGRAPH_URL = process.env.SUBGRAPH_URL;
```

**Bootstrapping flow:**
```
Facilitator startup
  → SUBGRAPH_URL configured? YES
    → Query subgraph: { feedbacks(first: 1000, skip: 0) { feedbackHash } }
    → Paginate through all results
    → Store each feedbackHash in Redis
    → Ready to serve — all past feedback is in Redis
  → SUBGRAPH_URL configured? NO
    → Redis-only dedup (local, no history)
```

**Runtime flow:**
```
Client submits feedback with ProofOfInteraction
  → Compute interactionHash
  → Check Redis: does interactionHash exist?
    → YES → 409 Conflict
    → NO → Process feedback → call giveFeedback(..., feedbackHash=interactionHash)
           → Store interactionHash in Redis
           → Subgraph indexes NewFeedback event
           → Other facilitators pick it up on next startup / periodic sync
```

---

## Task 7: E2E Test — Full Reputation Flow

**Files** (in `./`):
- Modify: `e2e/test_x402_payment.py`

- [ ] **Step 7a: Update E2E test to verify proof-of-interaction**

Extend the existing test to:
1. After payment, extract `agentSignature` and `dataHash` from the PAYMENT-RESPONSE header or response body
2. Verify the signature using `Account.recover_message`
3. Submit feedback with the proof-of-interaction hash
4. Verify dedup: submit the same proof again and expect a 409

```python
# After successful payment:
if payment_response:
    tx_hash = payment_response.transaction
    task_ref = f"eip155:8453/{tx_hash}"

    # Compute interaction hash
    data_hash = compute_data_hash(request_body, response_body)
    interaction_hash = compute_interaction_hash(task_ref, data_hash)

    # Verify agent's signature
    recovered = Account.recover_message(
        encode_defunct(hexstr=interaction_hash.hex()),
        signature=agent_signature_bytes,
    )
    assert recovered.lower() == agent_account.address.lower()

    # Submit feedback with proof
    result = await submit_feedback(
        fac_client, agent_id, 95, tx_hash,
        proof_of_interaction_hash=data_hash.hex(),
    )
    assert result.get("success")

    # Verify dedup: second submission with same proof should fail
    result2 = await submit_feedback(
        fac_client, agent_id, 95, tx_hash,
        proof_of_interaction_hash=data_hash.hex(),
    )
    assert not result2.get("success")
```

---

## Task 8: Documentation

**Files** (in `./`):
- Modify: `README.md`

- [ ] **Step 8a: Update README**

Add section documenting:
- How the reputation extension works end-to-end
- How agents enable response signing (env var + config)
- How clients verify signatures and submit feedback
- Proof-of-interaction flow diagram (ascii or mermaid)

---

## File Map Summary

| File | Location | Action |
|------|----------|--------|
| `extensions/reputation/__init__.py` | `./x402-sdk/python/x402/` | Create |
| `extensions/reputation/types.py` | `./x402-sdk/python/x402/` | Create |
| `extensions/reputation/signing.py` | `./x402-sdk/python/x402/` | Create |
| `extensions/reputation/server.py` | `./x402-sdk/python/x402/` | Create |
| `extensions/__init__.py` | `./x402-sdk/python/x402/` | Modify |
| `client_base.py` | `./x402-sdk/python/x402/` | Modify |
| `http/clients/httpx_client.py` | `./x402-sdk/python/x402/` | Modify |
| `http/middleware/fastapi_middleware.py` | `./x402-sdk/python/x402/` | Modify |
| `src/services/proofOfInteraction.ts` | `./` | Create |
| `src/services/dedupStore.ts` | `./` | Create |
| `src/services/reputationService.ts` | `./` | Modify |
| `src/config/env.ts` | `./` | Modify |
| `index.ts` | `./` | Modify |
| `subgraph/subgraph.yaml` | `./` | Create |
| `subgraph/schema.graphql` | `./` | Create |
| `subgraph/src/mapping.ts` | `./` | Create |
| `e2e/test_x402_payment.py` | `./` | Modify |
| `README.md` | `./` | Modify |

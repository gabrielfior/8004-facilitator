# ERC-8004 x x402 Integration — Ethereum Foundation Grant

<aside>
🗺️ Status: In Progress — Chat held, notes being documented

</aside>

---

## Problem Statement

x402 and ERC-8004 are currently completely separate standards with no coupling. The goal of this grant is to integrate them so they work together seamlessly.

---

## Key Constraints

- Do NOT change anything on the x402 v3 standard — otherwise developers won't migrate over
- All changes must live on the facilitator side — not on payer or payee

---

## First Step — OpenMID on Ethereum Mainnet

Adapt the OpenMID quickstart (openmid.xyz) to work on Ethereum Mainnet. This is the entry point for the integration work.

[](https://www.openmid.xyz/docs/quickstart)

[Implementation plan - OpenMid](https://www.notion.so/Implementation-plan-OpenMid-358860acb8c180dfa118d2f2355c7ef2?pvs=21)

---

### Notes Gabriel - to be confirmed

1. Assume agent has identities on chains A and B, and wallets on chains A and B
    1. Tx 1 goes through on chain A
    2. Reputation should also be written on chain A, since we have the txHash there (not trivial how to verify this on-chain, but still somewhat more reasonable than accepting reputation record for tx 1 on chain B
2. Agent registration - agent shares with client registration in multiple chains (makes sense, one wallet per chain)
    1. Problem is, reputation records are spread across many chains
    2. One option - using IPFS as the central registry for the agent, and having reputation entries appended to IPFS as well
    3. Note: ERC-8004 already supports multi-chain registration via a `registrations` array in the agent's registration file (hosted via tokenURI/agentURI):
       ```json
       {
         "registrations": [
           {"agentId": 42, "agentRegistry": "eip155:8453:0x8004A818BFB912233c491871b3d84c89A494BD9e"},
           {"agentId": 108, "agentRegistry": "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp:satiRkxEiwZ51cv8PRu8UMzuaqeaNU9jABo6oAFMsLe"}
         ]
       }
       ```
    4. This pattern is already used by PR #1024 and SATI — no IPFS registry needed
3. Q - What is agentId? Is it unique if same developer (with 1 wallet) has multiple agents?
    1. Can it be transferred?
    2. What is agent registry?
    4. Decision — one EOA per agent (simplest model)

       The EOA address IS the agent identity:
       1. Agent is simply an EOA — no registry, no NFT, no registration file
       2. Server signs x402 responses directly with the EOA's private key
       3. Same key receives payments
       4. Verification: `ecrecover(interactionHash, signature)` recovers the signer address — matches the expected agent identity
       5. Multiple agents → multiple EOAs, each with its own private key

       **Why not NFT (ERC-8004) model:** The NFT model requires facilitator registration, on-chain txns, and maintaining a registration file with `signers` entries. For this integration, the extra complexity isn't justified — the EOA approach gets us a working x402 reputation system with minimal overhead.

       **Trade-off accepted:** No on-chain reputation portability across providers. Reputation is tied to the EOA address directly. If cross-provider reputation is needed later, we can layer it on top.
4. Streaming responses aren’t supported by the implementation on (https://github.com/x402-foundation/x402/pull/1024/changes#diff-d3f69bf1a3c14fcc53824a6d8e40def0678afe78d2e536013c453d853f49f9d9R237) - can we avoid this somehow?
    Not possible without changing the spec. PR #1024 requires buffered responses (full body needed for dataHash). Servers using streaming (SSE, chunked) MUST NOT include reputation signing per spec §1.
5. What happens if server presents a `feedbackEndpoint` not matching its own? How can fakes be spotted?
6. Feedback endpoint - are there specs for that already? Which facilitator implementation should be extended (I imagine OpenMid)?
7. What is proof-of-interaction? Can it be verified on-chain? I guess only payment is enough, no?
    1. How does off-chain validation work for proof-of-interaction? (e.g., facilitator validates agentSig + reviewerSig + taskRef on-chain tx status before allowing feedback submission)
8. Open questions from PR #1024 / Issue #931 discussion:
     - **Mandatory response signing overhead** (MonteCrypto999): For $0.05-$0.15 micro-transactions, Ed25519 signature verification adds latency and cost. Alternative: facilitators as reputation authorities using ERC-8004's validation registry (no per-response signing required).
       **Decision: signatures mandatory, feedback submission optional.**
       - Pro signing required: without cryptographic proof, any feedback is untrustworthy — no way to verify the interaction happened. Every piece of feedback carries proof of real service delivery.
       - Pro optional: less friction, no key management for agents that don't care about reputation.
       - The $1 attack vector (pay for txns, leave fake feedback) is solved by **dedup** (one feedback per txHash per reviewer) + **facilitator attestation** (weighted trust signals), not by removing cryptographic proof.
       - Feedback submission optional → agents that don't participate in reputation simply don't submit feedback. No mandatory UX burden.
       - Conclusion: signatures protect system integrity; optional feedback protects agent flexibility.
     - [Gabriel won't do] **Cross-chain decoupling** (phdargen): Agent identity on chain X, payments on chain Y — how does the facilitator verify across chains? Related: SVM audit verification question.
     - [To be discussed] **Binary data handling** (phdargen): Request/response bodies may be binary — dataHash computation needs a clear encoding standard.
     - [to be discussed] **Facilitator attestation** (BranchManager69): Instead of proof-of-interaction, the facilitator itself attests that settlement happened (simpler, trusted model). PR #1054 implements this for Dexter facilitator.
     - [nothing to do] **Cold-start reputation** (douglasborthwick-crypto): New agents have no reputation. Pre-payment trust signals via wallet holdings (InsumerAPI, 31 EVM chains) before recording feedback.
     - **8004 service providers as 8004 identities** (MonteCrypto999): Attestors themselves should have ERC-8004 identities for trustlessness.
     - [out of scope] **Nostr Web of Trust** (joelklabo): Complementary off-chain reputation signal alongside on-chain proof-of-interaction.
     - [out of scope] **Avalanche C-Chain** (iJaack): ERC-8004 already live there — need to ensure compatibility.
     - **feedbackAggregator** (tenequm): Off-chain aggregator to offload gas costs of on-chain feedback submission.
9. [out of scope of initial implementation] ENS integration for agents? — possible future implementation
     - Map human-readable names (e.g., `my-agent.eth`) to agent EOA addresses
     - x402 `PaymentRequired` could return an ENS name instead of a raw address (resolved by client/facilitator)
     - ENS reverse resolution would let agents look each other up by name
     - CCIP (Cross-Chain Interoperability Protocol) via ENS could help multi-chain agent discovery
     - Low priority for initial implementation — adds a resolver dependency with no protocol-level benefit
10. It seems like work involves
    1. x402 extension (optional reputation as part of protocol)
    2. facilitator should support reputation calls by clients
    3. server should also return reputation as part of responses
    4. Optional — facilitator as recognized authority:
        - Facilitator maintains its own ERC-8004 identity key
        - Performs periodic agent validation (quality-of-work checks)
        - Issues `facilitatorAttestation` per agent (BranchManager69's PR #1054 model)
        - Clients query facilitator attestations as a weighted trust signal (supplements raw feedback)
        - Low effort, high value for reputation quality at scale

### Tech specs - Gabriel

- Next steps involve
    - x402 extension work
        - add new extension
    - facilitator
        - include on-chain entry for agent identity
    - client
        - have bool flag that adds a reputation score

## Open Points for Further Discussion

1. Settlement tokens — using tokens other than USDC for settlements
2. Agent registry layer — using IPFS for cross-chain registries (enabling portability across chains)
3. Mechs integration — using Gnosis Mechs so that an agent's wallet becomes portable (ref: github.com/gnosisguild/mech)
4. Facilitator attestation — Facilitator issues `facilitatorAttestation` as a weighted trust signal per agent (BranchManager69's PR #1054 model). Deferred: not needed for initial reputation integration, but valuable for reputation quality at scale.

---

## Current Efforts — Active Work

<aside>
🔧 The x402 team is actively working on ERC-8004 integration. Two key GitHub artifacts exist.

</aside>

### Issue #931 — ERC-8004 Agent Reputation Extension (Open)

Proposal by @notorious-d-e-v to integrate x402 payment protocol with ERC-8004 Trustless Agents spec, enabling on-chain reputation signals based on payment outcomes.

- Agents advertise on-chain identity (EVM or Solana) in PaymentRequired
- Clients optionally advertise identity in PaymentPayload
- After settlement, either party submits reputation feedback on-chain
- Multi-chain: EVM (Base, Ethereum) + Solana (SVM)
- Integrates via x402 hook system for post-settlement reputation submission

Key contributors: @ruhil6789 (EVM side) and @tenequm (Solana/SATI side). @MonteCrypto999 also active on Solana implementation.

[](https://github.com/x402-foundation/x402/issues/931)

### PR #1024 — Reputation Extension Specification (Open, Specs Label)

Implements Issue #931. Adds the `reputation` extension specification to x402.

- Agents sign every response with their registered identity key (proof of service delivery)
- Non-selective participation: agents sign BEFORE knowing feedback outcome (prevents gaming)
- Prevents fake feedback: reviews require cryptographic proof of actual service delivery
- Multi-chain: ERC-8004 compliant registries on EVM + Solana SATI program

Active review from @phdargen (aligning structure with ERC-8004 specs) and @tenequm (Solana/SATI integration). Extension implementations in progress — coordinating EVM and Solana sides.

[](https://github.com/x402-foundation/x402/pull/1024)

### Solana Backend: SATI (cascade-protocol)

SATI (Solana Agent Trust Infrastructure) by @tenequm is the Solana-side ERC-8004 compatible implementation. Already deployed to mainnet.

- Agent identity: Token-2022 NFT with TokenGroup (maps to AgentIdentity)
- Feedback: Solana Attestation Service + Light Protocol (~$0.002 per feedback)
- Cross-chain identity: link_evm_address with secp256k1 verification
- SDK: @cascade-fyi/sati-sdk
- Program ID: satiRkxEiwZ51cv8PRu8UMzuaqeaNU9jABo6oAFMsLe

[](https://github.com/cascade-protocol/sati)

---

## Open Technical Questions

- No canonical ERC-8004 on Solana — two implementations: SATI (cascade) and 8004-solana (QuantuLabs). SDK must handle registry selection.
- Feedback deduplication: indexers should deduplicate by txHash, counting only first feedback per unique transaction hash.
- Binary data handling in request/response flows (raised by @phdargen)
- ERC-8004 <-> SATI alignment: SATI is opinionated; needs to converge with EVM standard to avoid fragmentation.
- Extension implementation coordination: EVM (@ruhil6789) and Solana (@tenequm) working in parallel.

<aside>
🔗 SATI (Solana Agent Trust Infrastructure) by cascade-protocol — Solana-side ERC-8004 implementation. Our solution takes a similar approach but extends it to EVM chains via OpenMID. See: https://github.com/cascade-protocol/sati

</aside>

<aside>
🔗 x402 Foundation — ERC-8004 integration work (Issue #931, PR #1024). Our solution extends their approach by adding OpenMID for cross-chain portability on EVM chains. See: https://github.com/x402-foundation/x402/issues/931

</aside>
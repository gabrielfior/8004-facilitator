// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IReputationRegistry} from "./interfaces/IReputationRegistry.sol";

struct FeedbackParams {
    uint256 agentId;
    int128 value;
    uint8 valueDecimals;
    string tag1;
    string tag2;
    string endpoint;
    string feedbackURI;
    bytes32 feedbackHash;
}

interface IFeedbackGateway {
    function consumeInteractionHash(bytes32 interactionHash) external;
    function tryClaimSettlement(bytes32 settlementTxHash, address caller) external returns (bool);
}

/// @title FeedbackGateway
/// @notice EIP-7702 delegate + global x402 interaction dedup + settlement-payer reputation binding.
/// @dev Client EOA delegates to this contract's address, then calls submitFeedback.
///      All persistent state (dedup, settlement) is accessed via external calls to dedupStore
///      (which is address(this)), because EIP-7702 delegation runs code at the EOA's address
///      where local storage is isolated from the contract's storage.
contract FeedbackGateway {
    address public immutable dedupStore;

    mapping(bytes32 => bool) public usedHashes;
    mapping(bytes32 => address) public settlementPayer;
    mapping(bytes32 => bool) public usedSettlements;

    error DuplicateHash(bytes32 hash);
    error SettlementAlreadyUsed(bytes32 settlementTxHash);

    event SettlementRecorded(bytes32 indexed txHash, address indexed payer);

    constructor() {
        dedupStore = address(this);
    }

    function consumeInteractionHash(bytes32 interactionHash) external {
        if (usedHashes[interactionHash]) {
            revert DuplicateHash(interactionHash);
        }
        usedHashes[interactionHash] = true;
    }

    function hasBeenUsed(bytes32 interactionHash) external view returns (bool) {
        return usedHashes[interactionHash];
    }

    /// @notice Check if caller is the settlement payer and claim the settlement slot.
    /// @return True if caller is the verified payer (settlement is locked). False if no match.
    /// @dev This is called via external call from EIP-7702 delegated code to ensure
    ///      storage reads/writes go to the contract address, not the EOA.
    function tryClaimSettlement(bytes32 settlementTxHash, address caller) external returns (bool) {
        if (settlementPayer[settlementTxHash] == caller) {
            if (usedSettlements[settlementTxHash]) {
                revert SettlementAlreadyUsed(settlementTxHash);
            }
            usedSettlements[settlementTxHash] = true;
            return true;
        }
        return false;
    }

    /// @notice Record who paid for a settlement. Permissionless, first-writer wins.
    /// @param txHash The settlement transaction hash
    /// @param payer The address that paid (client EOA)
    function recordSettlement(bytes32 txHash, address payer) external {
        if (settlementPayer[txHash] == address(0)) {
            settlementPayer[txHash] = payer;
            emit SettlementRecorded(txHash, payer);
        }
    }

    /// @notice EIP-7702 entrypoint: atomic dedup + settlement verification + ERC-8004 giveFeedback.
    /// @param settlementTxHash The settlement tx hash (bytes32(0) for unverified feedback)
    function submitFeedback(
        address registry,
        FeedbackParams calldata params,
        bytes32 settlementTxHash
    ) external {
        if (settlementTxHash != bytes32(0)) {
            IFeedbackGateway(dedupStore).tryClaimSettlement(settlementTxHash, msg.sender);
        }

        IFeedbackGateway(dedupStore).consumeInteractionHash(params.feedbackHash);
        IReputationRegistry(registry).giveFeedback(
            params.agentId,
            params.value,
            params.valueDecimals,
            params.tag1,
            params.tag2,
            params.endpoint,
            params.feedbackURI,
            params.feedbackHash
        );
    }
}

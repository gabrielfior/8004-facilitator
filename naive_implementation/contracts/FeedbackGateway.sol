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
}

/// @title FeedbackGateway
/// @notice EIP-7702 delegate + global x402 interaction dedup (one deployment).
/// @dev Client EOA delegates to this contract's address, then calls submitFeedback.
///      External calls to dedupStore preserve global usedHashes; registry sees EOA as author.
///      Reputation is indexed via ReputationRegistry NewFeedback only (no duplicate gateway events).
contract FeedbackGateway {
    address public immutable dedupStore;

    mapping(bytes32 => bool) public usedHashes;

    error DuplicateHash(bytes32 hash);

    constructor() {
        dedupStore = address(this);
    }

    /// @notice Mark an interaction hash as used on the singleton store. Reverts if already consumed.
    function consumeInteractionHash(bytes32 interactionHash) external {
        if (usedHashes[interactionHash]) {
            revert DuplicateHash(interactionHash);
        }
        usedHashes[interactionHash] = true;
    }

    function hasBeenUsed(bytes32 interactionHash) external view returns (bool) {
        return usedHashes[interactionHash];
    }

    /// @notice EIP-7702 entrypoint: atomic dedup + ERC-8004 giveFeedback (client == msg.sender on registry).
    function submitFeedback(address registry, FeedbackParams calldata params) external {
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

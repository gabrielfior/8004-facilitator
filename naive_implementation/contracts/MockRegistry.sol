// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract MockRegistry {
    event FeedbackReceived(
        uint256 indexed agentId,
        address indexed client
    );

    function giveFeedback(
        uint256 agentId,
        int128,
        uint8,
        string calldata,
        string calldata,
        string calldata,
        string calldata,
        bytes32
    ) external {
        emit FeedbackReceived(agentId, msg.sender);
    }
}

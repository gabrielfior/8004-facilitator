// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract FeedbackGateway {
    mapping(bytes32 => bool) public usedHashes;

    function markUsed(bytes32 hash) external returns (bool) {
        if (usedHashes[hash]) {
            return false;
        }
        usedHashes[hash] = true;
        return true;
    }

    function unmarkUsed(bytes32 hash) external {
        usedHashes[hash] = false;
    }

    function isUsed(bytes32 hash) external view returns (bool) {
        return usedHashes[hash];
    }
}

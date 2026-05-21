// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import {FeedbackGateway, FeedbackParams} from "../contracts/FeedbackGateway.sol";
import {IReputationRegistry} from "../contracts/interfaces/IReputationRegistry.sol";

/// @dev ABI parity with official registry (see lib/erc-8004-contracts).
contract FeedbackGatewayTest is Test {
    FeedbackGateway internal gateway;
    address internal client;

    function setUp() public {
        gateway = new FeedbackGateway();
        client = makeAddr("client");
    }

    function test_dedupStore_is_deployed_address() public view {
        assertEq(gateway.dedupStore(), address(gateway));
    }

    function test_consume_then_hasBeenUsed() public {
        bytes32 hash = keccak256("interaction");
        gateway.consumeInteractionHash(hash);
        assertTrue(gateway.hasBeenUsed(hash));
    }

    function test_revert_duplicate() public {
        bytes32 hash = keccak256("interaction");
        gateway.consumeInteractionHash(hash);
        vm.expectRevert(abi.encodeWithSelector(FeedbackGateway.DuplicateHash.selector, hash));
        gateway.consumeInteractionHash(hash);
    }

    function test_reputation_interface_selector_matches_official() public pure {
        bytes4 sel = IReputationRegistry.giveFeedback.selector;
        assertEq(sel, bytes4(keccak256(
            "giveFeedback(uint256,int128,uint8,string,string,string,string,bytes32)"
        )));
    }

    /// @dev Simulates EIP-7702: client EOA runs gateway bytecode; dedup stays on singleton.
    function test_submitFeedback_delegated_EOA_uses_global_dedup() public {
        bytes32 hash = keccak256("delegated-interaction");
        vm.etch(client, address(gateway).code);

        FeedbackParams memory params = FeedbackParams({
            agentId: 1,
            value: 95,
            valueDecimals: 0,
            tag1: "x402",
            tag2: "weather",
            endpoint: "http://localhost",
            feedbackURI: "",
            feedbackHash: hash
        });

        address mockRegistry = address(new MockReputationRegistry());

        vm.prank(client);
        FeedbackGateway(client).submitFeedback(mockRegistry, params);

        assertTrue(gateway.hasBeenUsed(hash), "dedup on singleton");
        assertEq(MockReputationRegistry(mockRegistry).lastClient(), client, "registry author");
        assertEq(MockReputationRegistry(mockRegistry).callCount(), 1);
    }

    function test_submitFeedback_reverts_when_registry_reverts_and_hash_not_consumed() public {
        bytes32 hash = keccak256("revert-interaction");
        vm.etch(client, address(gateway).code);

        FeedbackParams memory params = FeedbackParams({
            agentId: 1,
            value: 95,
            valueDecimals: 0,
            tag1: "x402",
            tag2: "weather",
            endpoint: "http://localhost",
            feedbackURI: "",
            feedbackHash: hash
        });

        address mockRegistry = address(new RevertingReputationRegistry());

        vm.prank(client);
        vm.expectRevert("mock revert");
        FeedbackGateway(client).submitFeedback(mockRegistry, params);

        assertFalse(gateway.hasBeenUsed(hash), "consume rolled back with whole tx");
    }
}

contract MockReputationRegistry {
    address public lastClient;
    uint256 public callCount;

    function giveFeedback(
        uint256,
        int128,
        uint8,
        string calldata,
        string calldata,
        string calldata,
        string calldata,
        bytes32
    ) external {
        lastClient = msg.sender;
        callCount++;
    }
}

contract RevertingReputationRegistry {
    function giveFeedback(
        uint256,
        int128,
        uint8,
        string calldata,
        string calldata,
        string calldata,
        string calldata,
        bytes32
    ) external pure {
        revert("mock revert");
    }
}

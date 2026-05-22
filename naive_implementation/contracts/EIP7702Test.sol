// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract EIP7702Test {
    event Caller(address indexed msgSender, address indexed txOrigin);

    function check() external {
        emit Caller(msg.sender, tx.origin);
    }
}

// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockUSDC is ERC20 {
    uint8 private _decimals;
    bytes32 public DOMAIN_SEPARATOR;
    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );
    mapping(address => mapping(bytes32 => bool)) public authorizationState;

    constructor(string memory name_, string memory version_, uint8 decimals_) ERC20(name_, "USDC") {
        _decimals = decimals_;
        DOMAIN_SEPARATOR = keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256(bytes(name_)),
            keccak256(bytes(version_)),
            block.chainid,
            address(this)
        ));
    }

    function decimals() public view override returns (uint8) { return _decimals; }

    function mint(address to, uint256 amount) external { _mint(to, amount); }

    function transferWithAuthorization(
        address from, address to, uint256 value,
        uint256 validAfter, uint256 validBefore, bytes32 nonce,
        uint8 v, bytes32 r, bytes32 s
    ) external {
        require(validAfter <= block.timestamp, "not yet valid");
        require(validBefore >= block.timestamp, "expired");
        require(!authorizationState[from][nonce], "used");
        bytes32 digest = keccak256(abi.encodePacked(
            "\x19\x01", DOMAIN_SEPARATOR,
            keccak256(abi.encode(TRANSFER_WITH_AUTHORIZATION_TYPEHASH, from, to, value, validAfter, validBefore, nonce))
        ));
        require(ecrecover(digest, v, r, s) == from, "invalid sig");
        authorizationState[from][nonce] = true;
        _transfer(from, to, value);
    }
}

.PHONY: setup build start stop start-anvil start-facilitator start-agent-server run-client clean

RPC_URL ?= http://127.0.0.1:8545
ROOT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

ENV_FILE = /tmp/setup.env
read_env = $(shell grep $(1) $(ENV_FILE) 2>/dev/null | cut -d= -f2)

setup: build
	RPC_URL=$(RPC_URL) uv run python -m src.setup

build:
	cd "$(ROOT_DIR)" && forge build
	@echo "Contracts built."

# On Tenderly / remote RPC: make start (no anvil needed, RPC_URL points to Tenderly)
# On local Anvil fork:      make start-anvil first, then make start-services
start: start-services
	@echo "Services started (anvil must be running separately if using local fork)"

start-services: start-facilitator start-agent-server
	@echo "Facilitator + agent server started."

stop:
	@pkill -f "anvil" 2>/dev/null || true
	@pkill -f "src/facilitator" 2>/dev/null || true
	@pkill -f "src/agent_server" 2>/dev/null || true
	@sleep 1
	@echo "All services stopped."

start-anvil:
	@echo "Starting Anvil fork..."
	anvil --fork-url $(RPC_URL) --chain-id 1 &>/tmp/anvil.log &
	@sleep 2
	@echo "Anvil ready (PID: $$(pgrep -f 'anvil --fork' 2>/dev/null))"

start-facilitator:
	RPC_URL=$(RPC_URL) \
	NETWORK_ID=$(call read_env,NETWORK_ID) \
	FEEDBACK_GATEWAY=$(call read_env,FEEDBACK_GATEWAY) \
	uv run python -m src.facilitator &>/tmp/facilitator.log &
	@sleep 1
	@echo "Facilitator started (PID: $$!)"

start-agent-server:
	RPC_URL=$(RPC_URL) \
	NETWORK_ID=$(call read_env,NETWORK_ID) \
	AGENT_ADDRESS=$(call read_env,AGENT_ADDRESS) \
	uv run python -m src.agent_server &>/tmp/agent_server.log &
	@sleep 1
	@echo "Agent server started (PID: $$!)"

run-client:
	RPC_URL=$(RPC_URL) \
	NETWORK_ID=$(call read_env,NETWORK_ID) \
	EIP_7702_SUPPORTED=$${EIP_7702_SUPPORTED:-false} \
	CLIENT_PRIVATE_KEY=$(call read_env,CLIENT_KEY) \
	FEEDBACK_GATEWAY=$(call read_env,FEEDBACK_GATEWAY) \
	AGENT_ID=$(call read_env,AGENT_ID) \
	uv run python -m src.client

clean:
	rm -f $(ENV_FILE) /tmp/facilitator.log /tmp/agent_server.log

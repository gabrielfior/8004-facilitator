.PHONY: setup build start stop start-anvil start-facilitator start-agent-server run-client clean

RPC_URL ?= http://127.0.0.1:8545
ROOT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

setup: build
	RPC_URL=$(RPC_URL) uv run python -m src.setup

build:
	cd "$(ROOT_DIR)" && forge build
	@echo "Contracts built."

# On Tenderly / remote RPC: make start (no anvil needed, RPC_URL points to Tenderly)
# On local Anvil fork:          make start-anvil first, then make start-services
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
	FEEDBACK_GATEWAY=$$(grep FEEDBACK_GATEWAY /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	uv run python -m src.facilitator &>/tmp/facilitator.log &
	@sleep 1
	@echo "Facilitator started (PID: $$!)"

start-agent-server:
	RPC_URL=$(RPC_URL) \
	AGENT_ADDRESS=$$(grep AGENT_ADDRESS /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	uv run python -m src.agent_server &>/tmp/agent_server.log &
	@sleep 1
	@echo "Agent server started (PID: $$!)"

run-client:
	RPC_URL=$(RPC_URL) \
	EIP_7702_SUPPORTED=$${EIP_7702_SUPPORTED:-false} \
	CLIENT_PRIVATE_KEY=$$(grep CLIENT_KEY /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	FEEDBACK_GATEWAY=$$(grep FEEDBACK_GATEWAY /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	AGENT_ID=$$(grep AGENT_ID /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	uv run python -m src.client

clean:
	rm -f /tmp/setup.env /tmp/facilitator.log /tmp/agent_server.log

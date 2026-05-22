.PHONY: setup start stop run-client clean

RPC_URL ?= http://127.0.0.1:8545
ROOT_DIR := $(shell dirname $(realpath $(firstword $(MAKEFILE_LIST))))

setup: build
	python -m src.setup

build:
	cd "$(ROOT_DIR)" && forge build
	@echo "Contracts built."

start: start-anvil start-facilitator start-agent-server
	@echo "All services started."

stop:
	@pkill -f "anvil" 2>/dev/null || true
	@pkill -f "src/facilitator" 2>/dev/null || true
	@pkill -f "src/agent_server" 2>/dev/null || true
	@sleep 1
	@echo "All services stopped."

reload: stop start
	@echo "Services reloaded."

start-anvil:
	@echo "Starting Anvil fork..."
	anvil --fork-url $(RPC_URL) --chain-id 1 &>/tmp/anvil.log &
	@sleep 2
	@echo "Anvil ready (PID: $$(pgrep -f 'anvil --fork' 2>/dev/null))"

start-facilitator:
	FEEDBACK_GATEWAY=$$(grep FEEDBACK_GATEWAY /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	python -m src.facilitator &>/tmp/facilitator.log &
	@sleep 1
	@echo "Facilitator started (PID: $$!)"

start-agent-server:
	AGENT_ADDRESS=$$(grep AGENT_ADDRESS /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	python -m src.agent_server &>/tmp/agent_server.log &
	@sleep 1
	@echo "Agent server started (PID: $$!)"

run-client:
	CLIENT_PRIVATE_KEY=$$(grep CLIENT_KEY /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	FEEDBACK_GATEWAY=$$(grep FEEDBACK_GATEWAY /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	AGENT_ID=$$(grep AGENT_ID /tmp/setup.env 2>/dev/null | cut -d= -f2) \
	python -m src.client

clean:
	rm -f /tmp/setup.env /tmp/facilitator.log /tmp/agent_server.log

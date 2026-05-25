import os
import uvicorn
from src.shared.constants import SERVER_PORT

if __name__ == "__main__":
    agent_addr = os.getenv("AGENT_ADDRESS")
    if not agent_addr:
        print("Set AGENT_ADDRESS=<checksummed hex> from setup output")
        raise SystemExit(1)
    app = __import__("src.agent_server.app", fromlist=["create_app"]).create_app(agent_address=agent_addr)
    uvicorn.run(app, host="127.0.0.1", port=SERVER_PORT)

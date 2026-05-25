import asyncio
import os
from src.shared.constants import DEFAULT_CLIENT_KEY

if __name__ == "__main__":
    client_key = os.getenv("CLIENT_PRIVATE_KEY") or os.getenv("CLIENT_KEY") or DEFAULT_CLIENT_KEY
    gateway = os.getenv("FEEDBACK_GATEWAY")
    agent_id_str = os.getenv("AGENT_ID")
    if not gateway or not agent_id_str:
        print("Set FEEDBACK_GATEWAY=<address> and AGENT_ID=<int> from setup output")
        raise SystemExit(1)
    agent_id = int(agent_id_str)
    app = __import__("src.client.app", fromlist=["run_paying_client"])
    ok = asyncio.run(app.run_paying_client(
        client_key=client_key,
        feedback_gateway=gateway,
        agent_id=agent_id,
    ))
    print(f"\nClient result: {'SUCCESS' if ok else 'FAILED'}")
    raise SystemExit(0 if ok else 1)

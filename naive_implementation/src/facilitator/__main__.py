import os
import uvicorn
from src.shared.constants import FACILITATOR_PORT

if __name__ == "__main__":
    gateway = os.getenv("FEEDBACK_GATEWAY")
    if not gateway:
        print("Set FEEDBACK_GATEWAY=<address> from setup output")
        raise SystemExit(1)
    app = __import__("src.facilitator.app", fromlist=["create_app"]).create_app(feedback_gateway=gateway)
    uvicorn.run(app, host="127.0.0.1", port=FACILITATOR_PORT)


import { config } from "dotenv";
import express from "express";
import { paymentMiddleware } from "@x402/express";
import { HTTPFacilitatorClient, x402ResourceServer } from "@x402/core/server";
import { registerExactEvmScheme } from "@x402/evm/exact/server";

config();

const facilitatorUrl = process.env.FACILITATOR_URL || "http://localhost:4022";
const payTo = process.env.ADDRESS as `0x${string}`;

if (!payTo) {
  console.error("ADDRESS environment variable is not set");
  process.exit(1);
}

const app = express();
app.use(express.json());

const facilitatorClient = new HTTPFacilitatorClient({ url: facilitatorUrl });
const service = new x402ResourceServer(facilitatorClient);
registerExactEvmScheme(service);

app.use(
  paymentMiddleware(
    {
      "/weather": {
        accepts: {
          payTo,
          scheme: "exact",
          price: "$0.001",
          network: "eip155:11155111",
        },
      },
    },
    service,
  ),
);

app.get("/weather", (req, res) => {
  res.send({ report: { weather: "sunny", temperature: 70 } });
});

const PORT = 4021;
app.listen(PORT, () => {
  console.log(`Sepolia test server listening at http://localhost:${PORT}`);
});

import { registerCartRoutes } from "./routes/cart";
import { registerHealthRoute } from "./routes/health";
import { loadConfig } from "./config";

export function startServer(): void {
  const config = loadConfig();
  registerHealthRoute();
  registerCartRoutes(config.port);
  console.log(`listening on ${config.port}`);
}

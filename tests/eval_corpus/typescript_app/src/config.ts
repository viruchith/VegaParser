export interface AppConfig {
  port: number;
}

export function loadConfig(): AppConfig {
  return { port: Number(process.env.PORT ?? 8080) };
}

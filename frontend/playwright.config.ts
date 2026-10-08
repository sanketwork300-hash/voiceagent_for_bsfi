import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running stack:
 *   backend:  cd ../backend && docker compose up -d --wait
 *   frontend: npm run dev   (or: npm run build && npm start)
 */
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  globalSetup: "./tests/e2e/global-setup.ts",
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    channel: process.env.E2E_BROWSER_CHANNEL ?? "chrome",
  },
});

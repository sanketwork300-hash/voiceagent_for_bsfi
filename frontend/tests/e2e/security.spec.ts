import { expect, test } from "@playwright/test";
import { send, staffLogin, startCustomerChat } from "./helpers";

test("permission-gated pages show no data to roles without access", async ({ page }) => {
  await staffLogin(page, "agent@demo-bank.example", "/handoff");
  await page.goto("/policies");
  await expect(page.getByText("You don't have access to this")).toBeVisible();
  const res = await page.request.get("/api/backend/policies");
  expect(res.status()).toBe(403); // backend enforces it too
});

test("staff token is never readable by page scripts or stored in web storage", async ({ page }) => {
  await staffLogin(page);
  const exposed = await page.evaluate(() => document.cookie + JSON.stringify(localStorage) + JSON.stringify(sessionStorage));
  expect(exposed).not.toContain("bfsi_staff");
  expect(exposed).not.toMatch(/eyJ[\w-]+\.eyJ[\w-]+\.[\w-]+/);
  const cookie = (await page.context().cookies()).find((c) => c.name === "bfsi_staff");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Strict");
});

test("BFF rejects unauthenticated and cross-origin requests", async ({ page, request }) => {
  expect((await request.get("/api/backend/audit/events")).status()).toBe(401);
  await staffLogin(page);
  const cross = await page.request.post("/api/backend/policies", { headers: { Origin: "https://evil.example" }, data: { name: "x", effect: "DENY" } });
  expect(cross.status()).toBe(403);
  const demo = await request.post("/api/demo/customer-session", { headers: { Origin: "https://evil.example" }, data: {} });
  expect(demo.status()).toBe(403);
});

test("customer tokens are scoped to their own session; voice token needs a session", async ({ page, request, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  await startCustomerChat(page);
  const own = await page.evaluate(() => JSON.parse(sessionStorage.getItem("bfsi-customer-session") ?? "{}").state);
  const other = await (await request.post("/api/backend/sessions", { headers: { Origin: origin }, data: { tenant: "demo-bank" } })).json();
  const peek = await request.get(`/api/backend/sessions/${other.session.session_id}`, { headers: { "X-Session-Token": own.token } });
  expect(peek.status()).toBe(404);
  expect((await request.post("/api/backend/voice/token", { headers: { Origin: origin } })).status()).toBe(401);
});

test("WebSocket chat rejects an invalid token", async ({ page }) => {
  await page.goto("/");
  const code = await page.evaluate(() => new Promise<number>((resolve) => {
    const ws = new WebSocket("ws://localhost:8000/ws/chat/not-a-session?token=forged");
    ws.onclose = (e) => resolve(e.code);
  }));
  expect(code).toBe(1008);
});

test("message content is rendered as text, never executed", async ({ page }) => {
  let dialog = false;
  page.on("dialog", async (d) => { dialog = true; await d.dismiss(); });
  await startCustomerChat(page);
  await send(page, '<img src=x onerror="alert(1)"> what is the minimum balance?');
  await expect(page.getByText('<img src=x onerror="alert(1)">', { exact: false })).toBeVisible();
  expect(await page.locator("main img").count()).toBe(0);
  expect(dialog).toBe(false);
});

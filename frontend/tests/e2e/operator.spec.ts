import { expect, test } from "@playwright/test";
import { customerSessionSuffix, send, staffLogin, startCustomerChat } from "./helpers";

test("maker-checker: supervisor approves, then the customer confirms", async ({ browser }) => {
  const customer = await (await browser.newContext()).newPage();
  await startCustomerChat(customer);
  await send(customer, "Transfer ₹6,00,000 to Rahul");
  await customer.getByLabel("One-time password").fill("123456");
  await customer.getByRole("button", { name: "Verify" }).click();
  await expect(customer.getByText("Waiting for bank approval")).toBeVisible();
  const suffix = await customerSessionSuffix(customer);

  const supervisor = await (await browser.newContext()).newPage();
  await staffLogin(supervisor, "supervisor@demo-bank.example", "/approvals");
  const row = supervisor.getByRole("listitem", { name: `Approval for session ${suffix}` });
  await expect(row).toContainText("₹6,00,000");
  await row.getByRole("button", { name: "Approve" }).click();
  await supervisor.getByRole("button", { name: "Approve transfer" }).click();
  await expect(row).toHaveCount(0);

  await customer.getByRole("button", { name: "Check approval status" }).click();
  await customer.getByRole("button", { name: "Confirm transfer" }).click();
  await expect(customer.getByText("Transfer successful").first()).toBeVisible();
  await customer.context().close();
  await supervisor.context().close();
});

test("handoff desk: accept, reply, and the customer sees it live", async ({ browser }) => {
  const customer = await (await browser.newContext()).newPage();
  await startCustomerChat(customer);
  await send(customer, "Someone stole money from my account");
  await expect(customer.getByText(/Connecting you with a specialist/)).toBeVisible();
  const suffix = await customerSessionSuffix(customer);

  const agent = await (await browser.newContext()).newPage();
  await staffLogin(agent, "agent@demo-bank.example", "/handoff");
  await agent.getByRole("button", { name: new RegExp(`session ${suffix}`) }).click();
  await expect(agent.getByText("AI summary")).toBeVisible();
  await agent.getByRole("button", { name: "Accept handoff" }).click();
  await agent.getByLabel("Reply to the customer").fill("This is Asha from the fraud team. I've frozen the card.");
  await agent.getByRole("button", { name: "Send reply" }).click();
  await expect(customer.getByText("This is Asha from the fraud team")).toBeVisible();
  await expect(customer.getByText(/Specialist ·/)).toBeVisible();
  await customer.context().close();
  await agent.context().close();
});

test("operator chat shows the context panel with tool and policy detail", async ({ page }) => {
  await staffLogin(page, "admin@demo-bank.example", "/chat");
  await page.getByRole("button", { name: /Sign in as Priya/ }).click();
  await send(page, "What is my account balance?");
  const ctx = page.getByRole("complementary", { name: "Customer context" });
  await expect(ctx).toContainText("Priya Sharma");
  await expect(ctx).toContainText("get_account_balance");
  await page.getByRole("button", { name: /1 tool call/ }).click();
  await expect(page.getByText("Bank API (OpenAPI)")).toBeVisible();
});

test("tool test console runs through the policy engine", async ({ page }) => {
  await staffLogin(page, "admin@demo-bank.example", "/tools/transfer_money");
  await page.getByLabel("amount").fill("1000");
  await page.getByLabel("payee_name").fill("Rahul");
  await page.getByRole("button", { name: "Run test" }).click();
  await expect(page.getByText("Require auth")).toBeVisible(); // critical tool stops at the policy step
});

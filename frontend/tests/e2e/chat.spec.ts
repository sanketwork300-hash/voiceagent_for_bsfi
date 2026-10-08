import { expect, test } from "@playwright/test";
import { send, startCustomerChat } from "./helpers";

test("balance query uses the bank API and shows only plain-language services to customers", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "What is my account balance?");
  await expect(page.getByText(/₹12,85,230/).first()).toBeVisible();
  await expect(page.getByText("Account service")).toBeVisible();
  await expect(page.getByText("get_account_balance")).toHaveCount(0);
});

test("knowledge answer cites approved documents", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "What are home loan foreclosure charges?");
  await expect(page.getByText("Sources")).toBeVisible();
  await expect(page.getByRole("button", { name: /Home Loan Policy/ })).toBeVisible();
});

test("high-risk transfer: transaction OTP, explicit confirmation, receipt", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "Transfer ₹1,000 to Rahul");
  await expect(page.getByText("Additional verification required")).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm transfer" })).toHaveCount(0); // never before OTP
  await page.getByLabel("One-time password").fill("123456");
  await page.getByRole("button", { name: "Verify" }).click();
  const confirm = page.getByRole("button", { name: "Confirm transfer" });
  await expect(confirm).toBeVisible();
  await expect(page.getByText("₹1,000").first()).toBeVisible();
  await confirm.click();
  await expect(page.getByText("Transfer successful").first()).toBeVisible();
  await expect(page.getByText("Transaction ID")).toBeVisible();
  await expect(page.locator("main").getByText("123456")).toHaveCount(0); // OTP never displayed in the conversation
});

test("cancelling a confirmation changes nothing", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "Block my debit card");
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText(/Cancelled\. Nothing was changed/)).toBeVisible();
});

test("repeated wrong OTPs lock the action and hand off to a person", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "Transfer ₹2,000 to Rohan");
  for (const code of ["000001", "000002", "000003"]) {
    await page.getByLabel("One-time password").last().fill(code);
    await page.getByRole("button", { name: "Verify" }).last().click();
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'));
  }
  await expect(page.getByText(/Connecting you with a specialist/)).toBeVisible();
  await expect(page.getByText("Identity verification")).toBeVisible();
});

test("asking for a person shows the handoff with shared context", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "I want to talk to a human agent");
  await expect(page.getByText(/Connecting you with a specialist/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Context shared with the specialist" })).toContainText("Verification status");
});

test("Hinglish is detected without choosing a language", async ({ page }) => {
  await startCustomerChat(page);
  await send(page, "Mera credit card ka outstanding kitna hai?");
  await expect(page.getByText(/outstanding ₹23,450 hai/)).toBeVisible();
  await expect(page.getByText("Detected: Hinglish")).toBeVisible();
});

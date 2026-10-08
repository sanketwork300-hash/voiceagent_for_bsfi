import { expect, type Page } from "@playwright/test";

export const PASSWORD = "DemoBank!2026secure";

export async function staffLogin(page: Page, email = "admin@demo-bank.example", next = "/dashboard") {
  await page.goto(`/auth/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel("Organization ID").fill("demo-bank");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Verification code").fill("246810");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await page.waitForURL(`**${next}`);
}

export async function startCustomerChat(page: Page, path = "/assist") {
  await page.goto(path);
  await page.getByRole("button", { name: /Sign in as Priya/ }).click();
  await expect(page.getByText("Online")).toBeVisible();
}

export async function send(page: Page, text: string) {
  await page.locator("#composer").fill(text);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: 30_000 });
}

export async function customerSessionSuffix(page: Page): Promise<string> {
  const id = await page.evaluate(() => JSON.parse(sessionStorage.getItem("bfsi-customer-session") ?? "{}").state?.sessionId as string);
  return id.slice(-8);
}

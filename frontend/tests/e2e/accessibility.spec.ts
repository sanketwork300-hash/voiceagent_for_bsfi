import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { send, staffLogin, startCustomerChat } from "./helpers";

async function audit(page: Page, label: string) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${label}: ${v.id} — ${v.help} (${v.nodes.length})`)).toEqual([]);
}

test("customer screens have no serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  await audit(page, "start");
  await page.goto("/auth/login");
  await audit(page, "login");
  await startCustomerChat(page);
  await send(page, "Transfer ₹1,000 to Rahul");
  await expect(page.getByText("Additional verification required")).toBeVisible();
  await audit(page, "chat with verification slip");
  await page.goto("/assist/voice");
  await audit(page, "voice");
});

test("operator screens have no serious accessibility violations", async ({ page }) => {
  await staffLogin(page);
  await expect(page.getByText("Live activity")).toBeVisible();
  await audit(page, "dashboard");
  for (const [path, ready] of [["/tools", "transfer_money"], ["/policies/new", "Reads as"], ["/audit", "Verify chain integrity"], ["/handoff", "Waiting"]]) {
    await page.goto(path);
    await expect(page.getByText(ready).first()).toBeVisible();
    await audit(page, path);
  }
});

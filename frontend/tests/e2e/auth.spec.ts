import { expect, test } from "@playwright/test";
import { PASSWORD, staffLogin } from "./helpers";

test("unauthenticated console routes redirect to sign-in and return afterwards", async ({ page }) => {
  await page.goto("/audit");
  await expect(page).toHaveURL(/\/auth\/login\?next=%2Faudit/);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Verification code").fill("246810");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page).toHaveURL(/\/audit$/);
  await expect(page.getByRole("heading", { name: "Audit logs" })).toBeVisible();
});

test("wrong password and wrong MFA code are rejected with clear messages", async ({ page }) => {
  await page.goto("/auth/login");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.locator("#login-error")).toHaveText(/isn't right/);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Verification code").fill("000000");
  await page.getByRole("button", { name: "Verify and sign in" }).click();
  await expect(page.getByText(/That code didn't work/)).toBeVisible();
  await expect(page).toHaveURL(/\/auth\/mfa/);
});

test("sign out clears the session", async ({ page }) => {
  await staffLogin(page);
  await page.getByRole("button", { name: "Account" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/auth\/login/);
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/auth\/login/);
});

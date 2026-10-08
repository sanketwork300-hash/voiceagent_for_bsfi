import { expect, test } from "@playwright/test";

test("voice call runs on the shared conversation and continues in chat", async ({ page }) => {
  await page.goto("/assist/voice");
  await page.getByRole("button", { name: /Sign in as Priya/ }).click();
  await page.getByRole("button", { name: "Start call" }).click();
  const input = page.getByLabel("Type what you would say");
  await expect(input).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: /Listening|Connected/ })).toBeVisible();
  await input.fill("What is my loan balance?");
  await page.keyboard.press("Enter");
  const transcript = page.getByRole("region", { name: "Live transcript" });
  await expect(transcript).toContainText("28,45,000");

  // Barge-in: if the browser speaks the reply, the customer can interrupt immediately.
  const interrupt = page.getByRole("button", { name: "Interrupt" });
  if (await interrupt.isVisible({ timeout: 3000 }).catch(() => false)) {
    await interrupt.click();
    await expect(transcript).toContainText("Customer interruption");
  } else {
    test.info().annotations.push({ type: "note", description: "Headless browser produced no speech; interruption covered by unit tests." });
  }

  await page.getByRole("button", { name: "End call" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Call ended" })).toBeVisible();
  await page.getByRole("link", { name: "Continue in chat" }).click();
  await expect(page.getByText(/28,45,000/).first()).toBeVisible(); // the voice turn is in the chat history
});

test("keypad OTP during a call is never shown", async ({ page }) => {
  await page.goto("/assist/voice");
  await page.getByRole("button", { name: /Sign in as Priya/ }).click();
  await page.getByRole("button", { name: "Start call" }).click();
  await page.getByLabel("Type what you would say").fill("Transfer ₹1,000 to Rahul");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Verification required")).toBeVisible();
  await page.getByRole("button", { name: "Keypad" }).click();
  for (const d of "123456") await page.getByRole("button", { name: `Digit ${d}` }).click();
  await page.getByRole("button", { name: "Send code" }).click();
  const transcript = page.getByRole("region", { name: "Live transcript" });
  await expect(transcript).toContainText("Please confirm");
  await expect(transcript).not.toContainText("123456");
  await expect(transcript).toContainText("Keypad entry ••••••");
});

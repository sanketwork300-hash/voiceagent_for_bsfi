export default async function globalSetup() {
  // Sandbox only: restore the mock bank's fixtures (balances, cards) so action tests are repeatable.
  const bank = process.env.E2E_MOCK_BANK_URL ?? "http://127.0.0.1:8100";
  const res = await fetch(`${bank}/_admin/reset`, { method: "POST" }).catch((e: Error) => e);
  if (res instanceof Error || !res.ok) console.warn(`[e2e] could not reset the mock bank at ${bank}; balances may differ between runs`);
}

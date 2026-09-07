/** Resolve only the checkout ID from a handoff; send credentials to our configured API. */
export function checkoutDecisionUrl(handoffUrl: string, apiBase: string, storefrontOrigin: string): URL | null {
  try {
    const parsed = new URL(handoffUrl, storefrontOrigin);
    const match = parsed.pathname.match(/^\/api\/conto\/checkouts\/([a-f0-9]{36})$/);
    if (!match) return null;
    return new URL(`${apiBase}/conto/checkouts/${match[1]}/summary`, storefrontOrigin);
  } catch {
    return null;
  }
}

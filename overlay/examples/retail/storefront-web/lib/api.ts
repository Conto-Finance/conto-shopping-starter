// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import { AgentApi } from "web-shared";
import { checkoutDecisionUrl } from "./checkout-decision-url";
import type { CartPayload, Product, ProductDetails } from "./types";

export interface ContoControls {
  agent: { name: string; status: string; sessionScoped: boolean; expiresAt: string };
  spend: {
    currency: string;
    maxTransaction: number | null;
    approvalThreshold: number | null;
    dailyLimit: number | null;
    weeklyLimit: number | null;
    monthlyLimit: number | null;
    spent: { today: number; thisWeek: number; thisMonth: number };
    remaining: { today: number | null; thisWeek: number | null; thisMonth: number | null };
    walletCeilings: {
      maxTransaction: number | null;
      dailyLimit: number | null;
      weeklyLimit: number | null;
      monthlyLimit: number | null;
    };
    controlCeilings: {
      maxTransaction: number | null;
      approvalThreshold: number | null;
      dailyLimit: number | null;
      weeklyLimit: number | null;
      monthlyLimit: number | null;
    };
  };
  merchants: {
    mode: "RESTRICTED";
    current: { name: string; allowed: boolean; registered: boolean };
    registered: Array<{ name: string; status: string }>;
  };
  checkout: {
    label: string;
    settlement: string;
    spent: { today: number; thisWeek: number; thisMonth: number };
  };
  policies: Array<{
    name: string;
    description: string;
    status: string;
    rules: Array<{ type: string; value: unknown }>;
  }>;
  manage: { agent: string; policies: string; merchants: string };
}

export interface ContoControlUpdate {
  maxTransaction: number;
  approvalThreshold: number;
  dailyLimit: number;
  weeklyLimit: number;
  monthlyLimit: number;
  merchantAllowed: boolean;
  agentActive: boolean;
}

export interface ContoControlUpdateResult {
  controls: ContoControls | null;
  error: string | null;
}

export interface CheckoutDecisionCheck {
  key: string;
  label: string;
  status: "passed" | "review" | "blocked";
  detail: string;
  reason?: string;
}

export interface CheckoutDecisionSummary {
  status: string;
  outcome: "checking" | "approved" | "review" | "blocked" | "complete";
  checks: CheckoutDecisionCheck[];
  reasons: string[];
  action: { url: string; label: string } | null;
}

export const CHECKOUT_STATUS_EVENT = "conto:checkout-status";

// Production routes the API through the storefront's own origin. The upstream
// local launcher explicitly supplies http://localhost:8000.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export const api = new AgentApi(API_URL, "/api");

export const UNREACHABLE =
  "Couldn't reach the Anthropic Shopping API. Check the deployment or start the local demo and try again.";

export async function fetchProducts(): Promise<Product[] | null> {
  const data = await api.get<{ products: Product[] }>("/products", { limit: "100" });
  return data?.products ?? null;
}

export function fetchProduct(productId: string): Promise<ProductDetails | null> {
  return api.get<ProductDetails>(`/products/${encodeURIComponent(productId)}`);
}

export function fetchContoControls(): Promise<ContoControls | null> {
  return api.get<ContoControls>("/conto/controls");
}

export async function fetchCheckoutDecision(
  handoffUrl: string,
): Promise<CheckoutDecisionSummary | null> {
  try {
    const target = checkoutDecisionUrl(handoffUrl, api.base, window.location.origin);
    if (!target) return null;
    const response = await fetch(target, {
      cache: "no-store",
      headers: api.headers(),
    });
    if (!response.ok) return null;
    return (await response.json()) as CheckoutDecisionSummary;
  } catch {
    return null;
  }
}

function responseError(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const candidate = payload as { detail?: unknown; error?: unknown; message?: unknown };
  for (const value of [candidate.detail, candidate.error, candidate.message]) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return fallback;
}

export async function updateContoControls(update: ContoControlUpdate): Promise<ContoControlUpdateResult> {
  try {
    const response = await fetch(`${api.base}/conto/controls`, {
      method: "PATCH",
      headers: api.headers(true),
      body: JSON.stringify(update),
    });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      return {
        controls: null,
        error: responseError(payload, "Conto could not apply these controls. Try again."),
      };
    }
    return { controls: payload as ContoControls, error: null };
  } catch {
    return {
      controls: null,
      error: "Conto could not be reached. Checkout remains blocked until the controls reconnect.",
    };
  }
}

export async function addToCart(
  productId: string,
  quantity = 1,
): Promise<CartPayload | null> {
  const data = await api.post<{ cart: CartPayload }>("/cart/add", {
    product_id: productId,
    quantity,
  });
  return data?.cart ?? null;
}

export async function updateCartItem(
  productId: string,
  quantity: number,
): Promise<CartPayload | null> {
  const data = await api.post<{ cart: CartPayload }>("/cart/quantity", {
    product_id: productId,
    quantity,
  });
  return data?.cart ?? null;
}

export async function removeCartItem(productId: string): Promise<CartPayload | null> {
  const data = await api.post<{ cart: CartPayload }>("/cart/remove", {
    product_id: productId,
  });
  return data?.cart ?? null;
}

// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useId, useRef, useState } from "react";
import { formatMoney, safeHandoffs, useCatalogIndex } from "web-shared";
import {
  fetchCheckoutDecision,
  fetchProducts,
  CHECKOUT_STATUS_EVENT,
  type CheckoutDecisionCheck,
  type CheckoutDecisionSummary,
} from "@/lib/api";
import type { CheckoutPayload, Product } from "@/lib/types";
import { STORE_POLICY } from "@/lib/storePolicy";
import { DeliveryPromise, ProductImage } from "../ProductTile";

const CHECK_TONE: Record<CheckoutDecisionCheck["status"], string> = {
  passed: "bg-(--ok-soft) text-(--ok)",
  review: "bg-(--warn-soft) text-(--warn)",
  blocked: "bg-(--danger-soft) text-(--danger)",
};

const CHECK_MARK: Record<CheckoutDecisionCheck["status"], string> = {
  passed: "✓",
  review: "!",
  blocked: "×",
};

const CHECKOUT_POLL_MS = 2_000;
const CHECKOUT_POLL_WINDOW_MS = 10 * 60_000;

function isTerminal(decision: CheckoutDecisionSummary): boolean {
  return decision.outcome === "blocked" || decision.outcome === "complete";
}

function PurchaseChecks({ checks }: { checks: CheckoutDecisionCheck[] }) {
  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-(--line)">
      <div className="border-b border-(--line) bg-(--well)/60 px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-(--ink-soft)">
        Purchase checks
      </div>
      <ul className="divide-y divide-(--line)">
        {checks.map((check) => (
          <li key={check.key} className="flex items-start gap-2.5 px-3 py-2.5">
            <span
              aria-hidden
              className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full text-[12px] font-bold ${CHECK_TONE[check.status]}`}
            >
              {CHECK_MARK[check.status]}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold text-(--ink)">{check.label}</span>
              <span className="block text-[12px] leading-snug text-(--ink-soft)">{check.detail}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function decisionTitle(
  decision: CheckoutDecisionSummary | null,
  decisionFailed: boolean,
): string {
  if (decisionFailed) return "Unable to verify purchase";
  if (!decision) return "Checking purchase controls";
  if (decision.outcome === "blocked") return "Payment blocked";
  if (decision.outcome === "review") return "Approval required";
  if (decision.outcome === "complete") return "Purchase complete";
  if (decision.outcome === "approved") return "Secure checkout started";
  return "Ready for secure checkout";
}

function statusLabel(decision: CheckoutDecisionSummary | null): string {
  if (decision?.outcome === "complete") return "Purchase logged";
  if (decision?.outcome === "blocked") return "Payment stopped";
  if (decision?.outcome === "review") return "Awaiting decision";
  if (decision?.outcome === "approved") return "Checkout open";
  return "Not charged";
}

function statusNote(decision: CheckoutDecisionSummary | null): string {
  if (decision?.outcome === "complete") {
    return "Payment completed and was recorded against this session’s spend controls.";
  }
  if (decision?.outcome === "blocked") return "No payment was started.";
  if (decision?.outcome === "review") {
    return "Approve or deny in the review tab. This card updates automatically.";
  }
  if (decision?.outcome === "approved") {
    return "Complete the Stripe test checkout in its tab. This card updates automatically.";
  }
  return "Payment happens only after the checks above pass.";
}

export default function CheckoutSummary({ payload }: { payload: CheckoutPayload }) {
  const cart = payload.cart;
  const handoffs = safeHandoffs(payload.handoffs);
  const handoffUrl = handoffs[0]?.url ?? "";
  const [decision, setDecision] = useState<CheckoutDecisionSummary | null>(null);
  const [decisionFailed, setDecisionFailed] = useState(false);
  const notifiedDecision = useRef<string | null>(null);
  const handoffNoteId = useId();
  const catalog = useCatalogIndex(fetchProducts);
  const freeShipping = cart.subtotal > STORE_POLICY.freeShippingThreshold;

  useEffect(() => {
    let current = true;
    let timer: number | null = null;
    let reading = false;
    const startedAt = Date.now();
    setDecision(null);
    setDecisionFailed(false);
    if (!handoffUrl) return () => {
      current = false;
    };

    const readDecision = async () => {
      if (reading) return;
      reading = true;
      const result = await fetchCheckoutDecision(handoffUrl);
      reading = false;
      if (!current) return;
      setDecision((previous) => result ?? previous);
      setDecisionFailed(!result);
      if (
        (!result || !isTerminal(result)) &&
        Date.now() - startedAt < CHECKOUT_POLL_WINDOW_MS
      ) {
        timer = window.setTimeout(readDecision, CHECKOUT_POLL_MS);
      }
    };
    const readWhenVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (timer != null) window.clearTimeout(timer);
      timer = null;
      void readDecision();
    };

    void readDecision();
    window.addEventListener("focus", readWhenVisible);
    document.addEventListener("visibilitychange", readWhenVisible);
    return () => {
      current = false;
      if (timer != null) window.clearTimeout(timer);
      window.removeEventListener("focus", readWhenVisible);
      document.removeEventListener("visibilitychange", readWhenVisible);
    };
  }, [handoffUrl]);

  useEffect(() => {
    const outcome = decision?.outcome;
    const notificationKey = `${handoffUrl}:${outcome}`;
    if (
      (outcome === "blocked" || outcome === "complete") &&
      notifiedDecision.current !== notificationKey
    ) {
      notifiedDecision.current = notificationKey;
      window.dispatchEvent(
        new CustomEvent(CHECKOUT_STATUS_EVENT, { detail: { outcome } }),
      );
    }
  }, [decision?.outcome, handoffUrl]);

  const safeAction = decision?.action
    ? safeHandoffs([decision.action])[0] ?? null
    : null;
  const blockedReasons =
    decision?.outcome === "blocked"
      ? decision.reasons.length
        ? decision.reasons
        : ["This purchase does not meet the active controls."]
      : [];

  return (
    <section data-checkout-card className="rounded-2xl border-2 border-(--accent) bg-(--card) p-4 shadow-(--shadow-sm)">
      <div role="status" aria-live="polite" aria-atomic="true" className="flex items-center justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-(--ink)">
          {decisionTitle(decision, decisionFailed)}
        </h3>
        <span className="whitespace-nowrap rounded-full border border-(--line) bg-(--well)/60 px-2.5 py-0.5 text-[11px] font-semibold text-(--ink-soft)">
          {statusLabel(decision)}
        </span>
      </div>
      {payload.note ? <p className="mt-1 text-[13px] text-(--ink-soft)">{payload.note}</p> : null}

      <div className="mt-3 space-y-2 rounded-lg bg-(--well)/60 p-3 text-sm">
        {cart.items.map((item) => {
          const product: Product =
            catalog[item.product_id] ?? {
              product_id: item.product_id,
              title: item.title,
              price: item.price,
            };
          return (
            <div key={item.product_id} className="flex items-center gap-2.5">
              <ProductImage product={product} className="h-10 w-10 shrink-0 rounded-lg !text-xl" />
              <div className="min-w-0 flex-1">
                <div className="flex justify-between gap-2">
                  <span className="line-clamp-1 text-(--ink)" title={item.title}>
                    {item.title} × {item.quantity}
                  </span>
                  <span className="shrink-0 text-(--ink)">{formatMoney(item.line_total)}</span>
                </div>
                <DeliveryPromise product={product} />
              </div>
            </div>
          );
        })}
        <div className="flex justify-between border-t border-(--line) pt-2 text-base font-bold text-(--ink)">
          <span>Estimated total</span>
          <span>{formatMoney(cart.subtotal, cart.currency)}</span>
        </div>
        <div className="flex justify-between text-[12px] text-(--ink-soft)">
          <span>Shipping</span>
          <span>{freeShipping ? "Free" : "Calculated at checkout"}</span>
        </div>
      </div>

      {decision?.checks.length ? <PurchaseChecks checks={decision.checks} /> : null}

      {blockedReasons.length ? (
        <div role="alert" className="mt-3 rounded-lg border border-(--line) bg-(--danger-soft) px-3 py-2.5 text-[13px] leading-snug text-(--danger)">
          <strong>{blockedReasons.length === 1 ? "Reason" : "Reasons"}:</strong>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {blockedReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        </div>
      ) : null}

      {decisionFailed ? (
        <div role="alert" className="mt-3 rounded-lg border border-(--line) bg-(--danger-soft) px-3 py-2.5 text-[13px] text-(--danger)">
          Checkout status is temporarily unavailable. Retrying automatically.
        </div>
      ) : null}

      {safeAction ? (
        <a
          href={safeAction.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-describedby={handoffNoteId}
          className="mt-3 block w-full rounded-xl bg-(--accent) py-2.5 text-center text-sm font-bold text-(--ink)"
        >
          {safeAction.label}
        </a>
      ) : null}

      <p id={handoffNoteId} className="mt-2 text-center text-[11px] text-(--ink-soft)/80">
        {statusNote(decision)}
      </p>
    </section>
  );
}

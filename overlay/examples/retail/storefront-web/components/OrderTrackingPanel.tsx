// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect } from "react";
import { formatMoney, orderStatusLabel, type Order, useStoreFrame } from "web-shared";

const STEPS = ["Order confirmed", "Preparing shipment", "In transit", "Delivered"] as const;

const ACTIVE_STEP: Record<string, number> = {
  processing: 1,
  shipped: 2,
  delayed: 2,
  out_for_delivery: 2,
  delivered: 4,
  return_initiated: 4,
  refunded: 4,
};

const STATUS_TONE: Record<string, string> = {
  delayed: "bg-(--warn-soft) text-(--warn)",
  delivered: "bg-(--ok-soft) text-(--ok)",
  out_for_delivery: "bg-(--accent-soft) text-(--ink)",
  shipped: "bg-(--info-soft) text-(--info)",
};

function dateLabel(value?: string | null): string {
  const match = value?.match(/\d{4}-\d{2}-\d{2}/)?.[0];
  if (!match) return "To be confirmed";
  const [year, month, day] = match.split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

function estimateNote(value?: string | null): string | null {
  if (!value) return null;
  const match = /^\S+\s*\((.*)\)\s*$/.exec(value);
  return match?.[1] ?? null;
}

function trackingReference(orderId: string): string {
  return `ACME-${orderId.replace(/[^a-z0-9]/gi, "").toUpperCase()}`;
}

function updateFor(status: string): string {
  if (status === "processing") return "Your order is confirmed and being prepared for shipment.";
  if (status === "shipped") return "Your package is moving through the ACME delivery network.";
  if (status === "delayed") return "Your package is still in transit with a revised delivery estimate.";
  if (status === "out_for_delivery") return "Your package is with the local driver for delivery.";
  if (status === "delivered") return "Your package was delivered at its destination.";
  if (status === "cancelled") return "This order was cancelled before delivery.";
  if (status === "return_initiated") return "The return is in progress.";
  if (status === "refunded") return "The order was refunded.";
  return "The latest order status is available below.";
}

export default function OrderTrackingPanel({
  order,
  onClose,
}: {
  order: Order;
  onClose: () => void;
}) {
  const { ask } = useStoreFrame();
  const current = ACTIVE_STEP[order.status] ?? 0;
  const hasShipment = current >= 2;
  const delay = estimateNote(order.estimated_delivery);
  const first = order.items[0];
  const extraItems = Math.max(0, order.items.length - 1);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const askAgent = () => {
    onClose();
    ask(`Where is order ${order.order_id} right now? Summarize the latest delivery update and explain any delay.`);
  };

  return (
    <div className="fixed inset-0 z-[70] bg-black/35" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="order-tracking-title"
        className="absolute inset-y-0 right-0 flex w-[min(100%,440px)] flex-col border-l border-(--line) bg-(--card) shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-(--line) px-5 py-5">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-(--ink-soft)">Order {order.order_id}</p>
            <h2 id="order-tracking-title" className="mt-1 text-[24px] font-light tracking-[-0.025em] text-(--ink)">Delivery details</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close delivery details"
            className="grid h-9 w-9 place-items-center rounded-[4px] border border-(--line) text-[20px] text-(--ink-soft) transition hover:border-(--line-strong) hover:text-(--ink)"
          >
            ×
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          <section className="rounded-[4px] border border-(--line) bg-(--well)/60 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-[15px] font-semibold text-(--ink)">
                  {first?.title ?? `Order ${order.order_id}`}{extraItems ? ` + ${extraItems} more` : ""}
                </p>
                <p className="mt-1 text-[13px] text-(--ink-soft)">{formatMoney(order.total, order.currency)}</p>
              </div>
              <span className={`shrink-0 rounded-full px-2.5 py-1 text-[12px] font-semibold ${STATUS_TONE[order.status] ?? "bg-(--well) text-(--ink)"}`}>
                {orderStatusLabel(order.status)}
              </span>
            </div>
          </section>

          <section className="mt-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-(--ink-soft)">Latest update</p>
            <p className="mt-2 text-[16px] leading-relaxed text-(--ink)">{updateFor(order.status)}</p>
            {order.status === "delayed" && delay ? (
              <p className="mt-2 rounded-[4px] bg-(--warn-soft) px-3 py-2 text-[13px] leading-relaxed text-(--warn)">{delay}</p>
            ) : null}
          </section>

          <section className="mt-6" aria-label="Delivery progress">
            <ol>
              {STEPS.map((step, index) => {
                const complete = index < current;
                const active = index === current && current < STEPS.length;
                return (
                  <li key={step} className="relative flex min-h-14 gap-3 last:min-h-0">
                    {index < STEPS.length - 1 ? (
                      <span className={`absolute left-[7px] top-4 h-full w-px ${complete ? "bg-(--ok)" : "bg-(--line)"}`} aria-hidden="true" />
                    ) : null}
                    <span
                      className={`relative z-1 mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[9px] font-bold ${
                        complete
                          ? "bg-(--ok) text-(--card)"
                          : active
                            ? "border-[3px] border-(--ok) bg-(--card)"
                            : "border-2 border-(--line-strong) bg-(--card)"
                      }`}
                      aria-hidden="true"
                    >
                      {complete ? "✓" : ""}
                    </span>
                    <div className="pb-5">
                      <p className={`text-[14px] ${complete || active ? "font-semibold text-(--ink)" : "text-(--ink-soft)"}`} aria-current={active ? "step" : undefined}>
                        {step}
                      </p>
                      {index === 0 ? <p className="mt-0.5 text-[12px] text-(--ink-soft)">{dateLabel(order.placed_at)}</p> : null}
                      {index === STEPS.length - 1 && current === STEPS.length ? (
                        <p className="mt-0.5 text-[12px] text-(--ink-soft)">{dateLabel(order.estimated_delivery)}</p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>

          <dl className="mt-6 grid grid-cols-2 gap-px overflow-hidden rounded-[4px] border border-(--line) bg-(--line)">
            <div className="bg-(--card) p-3">
              <dt className="text-[11px] text-(--ink-soft)">{order.status === "delivered" ? "Delivered" : "Estimated delivery"}</dt>
              <dd className="mt-1 text-[13px] font-semibold text-(--ink)">{dateLabel(order.estimated_delivery)}</dd>
            </div>
            <div className="bg-(--card) p-3">
              <dt className="text-[11px] text-(--ink-soft)">Carrier</dt>
              <dd className="mt-1 text-[13px] font-semibold text-(--ink)">{hasShipment ? "ACME Delivery" : "Assigned at shipment"}</dd>
            </div>
            <div className="col-span-2 bg-(--card) p-3">
              <dt className="text-[11px] text-(--ink-soft)">Tracking reference</dt>
              <dd className="mt-1 font-mono text-[13px] font-semibold text-(--ink)">{hasShipment ? trackingReference(order.order_id) : "Available after shipment"}</dd>
            </div>
          </dl>
        </div>

        <footer className="border-t border-(--line) p-5">
          <button
            type="button"
            onClick={askAgent}
            className="w-full rounded-[4px] bg-(--ink) px-4 py-3 text-[13px] font-semibold text-(--card) transition hover:opacity-90"
          >
            Ask the agent about this delivery
          </button>
        </footer>
      </aside>
    </div>
  );
}

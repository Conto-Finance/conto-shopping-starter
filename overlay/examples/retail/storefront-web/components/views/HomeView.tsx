// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import {
  ArrivingPanel,
  estimateOf,
  formatMoney,
  HomeSection,
  type Order,
  plural,
  type Starter,
  Starters,
  upcoming,
  useCatalogIndex,
  useStoreFrame,
} from "web-shared";
import { fetchProducts, type ContoControls } from "@/lib/api";
import { NOUNS, OrderThumb } from "@/lib/orders";
import type { Product } from "@/lib/types";
import ContoMark from "../ContoMark";
import ProductTile from "../ProductTile";

const STARTERS: Starter[] = [
  { icon: "search", prompt: "Find a family camping tent under $250" },
  { icon: "home", prompt: "Build a home office setup for about $800" },
  { icon: "tag", prompt: "Compare drip and espresso for busy mornings" },
  { icon: "edit", prompt: "Remember: small home, neutral style, no outdoor storage" },
];

/** What the store is featuring: labelled bestseller or new, photographed ones first. */
function featured(catalog: Record<string, Product>): Product[] {
  return Object.values(catalog)
    .filter((product) => product.labels?.some((label) => label === "bestseller" || label === "new") && product.in_stock !== false)
    .sort((a, b) => Number(Boolean(b.image_url)) - Number(Boolean(a.image_url)))
    .slice(0, 4);
}

function Brief({ orders }: { orders: Order[] | null }) {
  if (!orders) return <>Ask for a recommendation, comparison, complete cart, or order update.</>;
  const open = upcoming(orders);
  if (!open.length) return <>No deliveries need attention. Your agent is ready for a new shopping task.</>;
  const late = open.filter((order) => order.status === "delayed");
  const next = estimateOf(open.find((order) => order.status !== "delayed") ?? open[0])?.date;
  return (
    <>
      {plural(open.length, "order")} on the way{next ? `; the next arrives ${next}` : ""}.{" "}
      {late.length ? <span className="font-semibold text-(--warn)">{late.length === 1 ? "One needs" : `${late.length} need`} attention.</span> : null}
    </>
  );
}

function ControlSnapshot({ controls }: { controls: ContoControls | null }) {
  const currency = controls?.spend.currency ?? "USD";
  const status = controls?.agent.status === "ACTIVE" ? "Ready" : controls ? "Paused" : "Connecting";
  const merchant = controls?.merchants.current.allowed ? "Approved" : controls ? "Blocked" : "Checking";
  return (
    <div className="agent-brief-grid" aria-label="Live Conto controls">
      <div className="agent-brief-item">
        <span>Control layer</span>
        <strong><i aria-hidden className={`status-dot ${controls?.agent.status === "ACTIVE" ? "is-ok" : ""}`} />{status}</strong>
      </div>
      <div className="agent-brief-item">
        <span>Per purchase</span>
        <strong>{controls ? formatMoney(controls.spend.maxTransaction ?? 0, currency) : "—"}</strong>
      </div>
      <div className="agent-brief-item">
        <span>Human approval</span>
        <strong>{controls ? `Over ${formatMoney(controls.spend.approvalThreshold ?? 0, currency)}` : "—"}</strong>
      </div>
      <div className="agent-brief-item">
        <span>ACME merchant</span>
        <strong>{merchant}</strong>
      </div>
    </div>
  );
}

export default function HomeView({
  controls,
  orders,
  ordersFailed,
  onSeeOrders,
}: {
  controls: ContoControls | null;
  orders: Order[] | null;
  ordersFailed: boolean;
  onSeeOrders: () => void;
}) {
  const { ask } = useStoreFrame();
  const catalog = useCatalogIndex(fetchProducts);
  const picks = featured(catalog);
  return (
    <div className="flex flex-col gap-5">
      <section className="agent-hero ac-reveal overflow-hidden border border-(--line)">
        <div className="agent-hero-copy">
          <div className="mb-7 flex items-center justify-between gap-4">
            <span className="flex min-w-0 items-center gap-2.5">
              <span className="conto-mark-tile grid h-7 w-7 shrink-0 place-items-center" aria-hidden="true">
                <ContoMark className="h-[18px] w-[18px]" />
              </span>
              <span className="min-w-0">
                <span className="brand-meta block">Conto control layer</span>
                <span className="mt-1 block truncate text-[11.5px] font-medium text-(--ink-2)">Anthropic commerce agent</span>
              </span>
            </span>
            <span className="live-demo-badge">
              <i aria-hidden />
              Live demo
            </span>
          </div>
          <h1 className="max-w-[620px] text-[32px] font-light leading-[1.05] tracking-[-0.035em] text-(--ink) sm:text-[40px]">
            Shop with an agent,
            <br />
            <em className="brand-headline-accent">spend within bounds</em>
          </h1>
          <p className="mt-4 max-w-[62ch] text-[14.5px] leading-relaxed text-(--ink-2)">
            Tell it what you need. The agent searches, compares, and builds the cart. Conto checks the merchant, spend limits, and approval rules before checkout.
          </p>
          <p className="mt-4 border-t border-(--line) pt-3 text-[12.5px] leading-relaxed text-(--ink-soft)">
            <span className="mr-2 font-medium text-(--ink)">Today</span>
            <Brief orders={orders} />
          </p>
        </div>
        <ControlSnapshot controls={controls} />
      </section>

      <section>
        <div className="mb-2.5 flex items-baseline justify-between gap-3">
          <div>
            <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-(--ink)">Shopping briefs</h2>
            <p className="mt-0.5 text-[12.5px] text-(--ink-soft)">Start with a goal, budget, or comparison.</p>
          </div>
        </div>
        <Starters items={STARTERS} />
      </section>

      <ArrivingPanel orders={orders} failed={ordersFailed} nouns={NOUNS} thumb={(order) => <OrderThumb order={order} />} onSeeAll={onSeeOrders} />
      {picks.length ? (
        <HomeSection title="Popular right now" subtitle="Open any product to ask your agent about it">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {picks.map((product) => (
              <ProductTile key={product.product_id} product={product} fluid onOpen={(item) => ask(`Tell me about the ${item.title}.`)} />
            ))}
          </div>
        </HomeSection>
      ) : null}
    </div>
  );
}

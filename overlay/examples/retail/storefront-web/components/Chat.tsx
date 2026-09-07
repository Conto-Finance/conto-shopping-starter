// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import type { ReactNode } from "react";
import {
  ActivityLine,
  type AgentTurn,
  type AssistantChatItem,
  Chat as ChatShell,
  formatMoney,
  type Order,
} from "web-shared";
import { addToCart } from "@/lib/api";
import type { CartPayload } from "@/lib/types";
import GenerativeBlock from "./generative";

const WIDE = new Set(["comparison", "plan"]);

/** Shimmers where the carousel will land while a search runs. */
function Pending({ item }: { item: AssistantChatItem }) {
  const searching = item.tools.includes("search_products") && !item.segments.some((s) => s.type === "ui");
  if (!searching) return <ActivityLine item={item} />;
  return (
    <section role="status" className="rounded-2xl border border-(--line) bg-(--card) p-3 shadow-(--shadow-sm)">
      <div className="mb-3 animate-pulse text-[15px] text-(--ink-soft)">{item.activity ?? "Searching the catalog…"}</div>
      <div className="flex gap-3 overflow-hidden pb-1">
        {[0, 1, 2, 3].map((slot) => (
          <div key={slot} className="ac-skeleton h-[150px] w-48 shrink-0 rounded-xl" />
        ))}
      </div>
    </section>
  );
}

/** The checkout card is the definitive end of a checkout reply. */
function checkoutFirstFeed(chat: AgentTurn): AgentTurn {
  return {
    ...chat,
    items: chat.items.map((item) => {
      if (item.kind !== "assistant") return item;
      const checkoutIndex = item.segments.findIndex(
        (segment) => segment.type === "ui" && segment.block.component === "checkout",
      );
      if (checkoutIndex < 0) return item;
      return {
        ...item,
        segments: item.segments.slice(0, checkoutIndex + 1),
        suggestions: [],
      };
    }),
  };
}

function PurchaseReceipt({
  order,
  onSeeOrders,
}: {
  order: Order;
  onSeeOrders: () => void;
}) {
  const itemCount = order.items.reduce((count, item) => count + item.quantity, 0);
  const title = order.items[0]?.title ?? "Completed purchase";
  const rest = order.items.length - 1;
  return (
    <section
      aria-label={`Order ${order.order_id} recorded`}
      className="rounded-2xl border border-(--line-strong) bg-(--card) p-4 shadow-(--shadow-sm)"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-(--ok)">
            Order recorded
          </p>
          <h3 className="mt-1 text-[15px] font-semibold text-(--ink)">
            {title}{rest > 0 ? ` + ${rest} more` : ""}
          </h3>
          <p className="mt-1 text-[12px] text-(--ink-soft)">
            {order.order_id} · {itemCount} {itemCount === 1 ? "item" : "items"} · Processing
          </p>
        </div>
        <strong className="text-[15px] tabular-nums text-(--ink)">
          {formatMoney(order.total, order.currency)}
        </strong>
      </div>
      <button
        type="button"
        onClick={onSeeOrders}
        className="mt-3 rounded-lg border border-(--line-strong) bg-(--well)/50 px-3 py-2 text-[12px] font-semibold text-(--ink) transition hover:border-(--accent-strong)"
      >
        View in Orders
      </button>
    </section>
  );
}

export default function Chat({
  chat,
  home,
  latestPurchase,
  onCartUpdate,
  onSeeOrders,
}: {
  chat: AgentTurn;
  home: ReactNode;
  latestPurchase: Order | null;
  onCartUpdate: (cart: CartPayload) => void;
  onSeeOrders: () => void;
}) {
  const feed = checkoutFirstFeed(chat);
  return (
    <ChatShell
      chat={feed}
      home={home}
      footer={
        latestPurchase
          ? <PurchaseReceipt order={latestPurchase} onSeeOrders={onSeeOrders} />
          : null
      }
      wide={WIDE}
      renderPending={(item) => <Pending item={item} />}
      renderBlock={(segment) => (
        <GenerativeBlock
          block={segment.block}
          status={segment.status}
          onAdd={async (product) => {
            const cart = await addToCart(product.product_id);
            if (cart) onCartUpdate(cart);
            return cart !== null;
          }}
        />
      )}
    />
  );
}

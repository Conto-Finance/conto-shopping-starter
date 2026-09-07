// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type AgentEvent,
  formatMoney,
  type Order,
  OrdersView,
  plural,
  StoreShell,
  type StoreView,
  upcoming,
  useAgentTurn,
  useResource,
  useSession,
} from "web-shared";
import CartPanel from "@/components/CartPanel";
import Chat from "@/components/Chat";
import ContoMark from "@/components/ContoMark";
import OrderTrackingPanel from "@/components/OrderTrackingPanel";
import HomeView from "@/components/views/HomeView";
import ControlsView from "@/components/views/ControlsView";
import { api, CHECKOUT_STATUS_EVENT, fetchContoControls, UNREACHABLE, type ContoControls } from "@/lib/api";
import { ORDER_TRACKING_EVENT, type OrderTrackingEventDetail } from "@/lib/order-tracking";
import { NOUNS, OrderThumb } from "@/lib/orders";
import type { CartPayload } from "@/lib/types";

type View = "assistant" | "orders" | "controls";

const ASSISTANT = "Shopping agent";

function Wordmark({ onRefresh }: { onRefresh: () => void }) {
  return (
    <button
      type="button"
      onClick={onRefresh}
      aria-label="Refresh shopping chat"
      title="Refresh shopping chat"
      className="agent-wordmark flex items-center gap-2.5 rounded-[4px] pr-1 text-left transition-opacity hover:opacity-75 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--accent-strong)"
    >
      <span className="conto-mark-tile grid h-8 w-8 place-items-center" aria-hidden="true">
        <ContoMark className="h-[21px] w-[21px]" />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[14.5px] font-semibold leading-none tracking-[-0.02em] text-(--ink)">Conto</span>
        <span className="agent-wordmark-product brand-meta mt-1 block whitespace-nowrap text-[8.5px] leading-none">Anthropic Shopping</span>
      </span>
    </button>
  );
}

function GuardrailBanner({ controls, failed, onOpen }: {
  controls: ContoControls | null;
  failed: boolean;
  onOpen: () => void;
}) {
  const active = controls?.agent.status === "ACTIVE";
  const merchantAllowed = controls?.merchants.current.allowed;
  const healthy = active && merchantAllowed;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="policy-banner group flex min-h-10 w-full items-center gap-3 border-b border-(--line) px-4 py-2 text-left sm:px-5"
      aria-label="Open Conto shopping controls"
    >
      <span className={`relative flex h-2 w-2 shrink-0 rounded-full ${failed ? "bg-(--danger)" : controls ? healthy ? "bg-(--ok)" : "bg-(--warn)" : "bg-(--ink-faint)"}`} />
      <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-(--ink-2)">
        <strong className="font-semibold text-(--ink)">{failed ? "Conto unavailable · checkout paused" : controls ? healthy ? "Conto controls active" : "Conto controls need attention" : "Connecting to Conto…"}</strong>
        {controls ? <span className="hidden sm:inline"> · {controls.merchants.current.name} {merchantAllowed ? "approved" : "blocked"}</span> : null}
      </span>
      {controls ? (
        <span className="hidden items-center gap-3 text-[11.5px] tabular-nums text-(--ink-soft) md:flex">
          <span>Approval over <strong className="font-semibold text-(--ink)">{formatMoney(controls.spend.approvalThreshold ?? 0, controls.spend.currency)}</strong></span>
          <span>Per purchase <strong className="font-semibold text-(--ink)">{formatMoney(controls.spend.maxTransaction ?? 0, controls.spend.currency)}</strong></span>
        </span>
      ) : null}
      <span className="rounded-[4px] border border-(--line-strong) bg-(--card) px-2.5 py-1 text-[10.5px] font-semibold text-(--ink) transition group-hover:border-(--accent-strong)">
        Edit controls
      </span>
    </button>
  );
}

export default function StorefrontPage() {
  const session = useSession(api);
  const [view, setView] = useState<View>("assistant");
  const [cart, setCart] = useState<CartPayload | null>(null);
  // A staged checkout owns the panel's primary action until the cart changes again.
  const [checkoutStaged, setCheckoutStaged] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [controls, setControls] = useState<ContoControls | null>(null);
  const [controlsFailed, setControlsFailed] = useState(false);
  const [ordersRevision, setOrdersRevision] = useState(0);
  const [trackingOrder, setTrackingOrder] = useState<Order | null>(null);
  const refreshChat = useCallback(() => window.location.reload(), []);
  const closeTracking = useCallback(() => setTrackingOrder(null), []);

  const handleCartUpdate = useCallback((next: CartPayload) => {
    setCart(next);
    setCheckoutStaged(false);
  }, []);

  const handleControlsUpdated = useCallback((next: ContoControls) => {
    setControls(next);
    setCheckoutStaged(false);
  }, []);

  const onEvent = useCallback(
    (event: AgentEvent) => {
      if (event.type === "cart_update") handleCartUpdate(event.data.cart as CartPayload);
      else if (event.type === "ui" && event.data.component === "checkout") setCheckoutStaged(true);
    },
    [handleCartUpdate],
  );

  const chat = useAgentTurn(api, { ...session, unreachable: UNREACHABLE, onEvent });
  // A reply may have started a return, so orders re-read after each one.
  const { data: orders, failed: ordersFailed } = useResource(
    session.sessionId ? () => api.fetchOrders() : null,
    [session.sessionId, chat.completed, ordersRevision],
  );
  useEffect(() => {
    if (!session.sessionId) return;
    let current = true;
    // Let the lightweight cart, order, and memory reads finish before first-time
    // Conto provisioning occupies the serverless backend.
    const timer = window.setTimeout(() => {
      void fetchContoControls().then((next) => {
        if (!current) return;
        setControls(next);
        setControlsFailed(!next);
      });
    }, 650);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [session.sessionId, chat.completed]);

  useEffect(() => {
    if (session.sessionId) void api.fetchCart<CartPayload>().then((next) => next && setCart(next));
  }, [session.sessionId]);

  useEffect(() => {
    const openTracking = (event: Event) => {
      const order = (event as CustomEvent<OrderTrackingEventDetail>).detail?.order;
      if (order?.order_id) setTrackingOrder(order);
    };
    window.addEventListener(ORDER_TRACKING_EVENT, openTracking);
    return () => window.removeEventListener(ORDER_TRACKING_EVENT, openTracking);
  }, []);

  useEffect(() => {
    if (!session.sessionId) return;
    const refreshCompletedSpend = (event: Event) => {
      const outcome = (event as CustomEvent<{ outcome?: string }>).detail?.outcome;
      if (outcome === "blocked" || outcome === "complete") {
        setCheckoutStaged(false);
      }
      if (outcome !== "complete") return;
      setOrdersRevision((revision) => revision + 1);
      void api.fetchCart<CartPayload>().then((next) => {
        if (next) handleCartUpdate(next);
      });
      void fetchContoControls().then((next) => {
        if (!next) return;
        setControls(next);
        setControlsFailed(false);
      });
    };
    window.addEventListener(CHECKOUT_STATUS_EVENT, refreshCompletedSpend);
    return () => window.removeEventListener(CHECKOUT_STATUS_EVENT, refreshCompletedSpend);
  }, [session.sessionId, handleCartUpdate]);

  useEffect(() => {
    if (!session.sessionId) return;
    const refreshOrders = () => {
      if (document.visibilityState === "visible") {
        setOrdersRevision((revision) => revision + 1);
      }
    };
    window.addEventListener("focus", refreshOrders);
    document.addEventListener("visibilitychange", refreshOrders);
    return () => {
      window.removeEventListener("focus", refreshOrders);
      document.removeEventListener("visibilitychange", refreshOrders);
    };
  }, [session.sessionId]);

  const late = orders?.filter((order) => order.status === "delayed").length ?? 0;
  const views: StoreView<View>[] = [
    { id: "assistant", label: "Shop", icon: "spark" },
    { id: "orders", label: "Orders", icon: "box", attention: late ? { count: late, label: `${late} delayed` } : null },
    { id: "controls", label: "Controls", icon: "check" },
  ];
  const shopper = session.shopper ?? { name: "Demo shopper" };
  const count = cart?.item_count ?? 0;
  const latestPurchase: Order | null =
    orders?.find((order) => order.order_id.startsWith("AS-")) ?? null;

  return (
    <StoreShell
      brand={<Wordmark onRefresh={refreshChat} />}
      views={views}
      view={view}
      onViewChange={setView}
      onHomeClick={refreshChat}
      chat={chat}
      api={api}
      assistantName={ASSISTANT}
      shopper={shopper}
      bag={{ label: "Cart", count, noun: "item", figure: count ? formatMoney(cart?.subtotal ?? 0, cart?.currency) : null }}
      panel={<CartPanel cart={cart} checkoutStaged={checkoutStaged} onCartUpdate={handleCartUpdate} />}
      panelOpen={panelOpen}
      onPanelOpenChange={setPanelOpen}
      placeholder={view === "orders" ? "Ask about an order, return, or delivery…" : "Tell your shopping agent what you need…"}
      banner={<GuardrailBanner controls={controls} failed={controlsFailed} onOpen={() => setView("controls")} />}
    >
      {/* The conversation stays mounted under the other view so its cards keep their state. */}
      <div className={view === "assistant" ? "h-full" : "hidden"}>
        <Chat
          chat={chat}
          latestPurchase={latestPurchase}
          onCartUpdate={handleCartUpdate}
          onSeeOrders={() => setView("orders")}
          home={
            <HomeView
              controls={controls}
              orders={orders}
              ordersFailed={ordersFailed}
              onSeeOrders={() => setView("orders")}
            />
          }
        />
      </div>
      {view === "orders" ? (
        <OrdersView
          orders={orders}
          failed={ordersFailed}
          nouns={NOUNS}
          subtitle={
            orders
              ? late
                ? `${plural(late, "order")} running late. Ask why, or ask about a return on anything delivered.`
                : `${plural(upcoming(orders).length, "order")} on the way. Ask about any of them, or about a return on anything delivered.`
              : undefined
          }
          thumb={(order) => <OrderThumb order={order} />}
          onOpenOrder={setTrackingOrder}
        />
      ) : null}
      {view === "controls" ? <ControlsView controls={controls} failed={controlsFailed} onUpdated={handleControlsUpdated} /> : null}
      {trackingOrder ? <OrderTrackingPanel order={trackingOrder} onClose={closeTracking} /> : null}
    </StoreShell>
  );
}

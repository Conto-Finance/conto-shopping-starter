// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useState } from "react";
import { AskLink, BagPanel, formatMoney, optionValuesLabel, Pill, plural, RemoveLink, Stepper, TotalRow, useCatalogIndex, useStoreFrame } from "web-shared";
import { api, fetchProducts, removeCartItem, updateCartItem } from "@/lib/api";
import { STORE_POLICY } from "@/lib/storePolicy";
import type { CartItem, CartPayload, Product } from "@/lib/types";
import { DeliveryPromise, ProductImage, ProductTitle } from "./ProductTile";
import ContoMark from "./ContoMark";

/** The policy says "over" the threshold, so a cart at exactly the threshold is not free. */
function FreeShippingMeter({ subtotal }: { subtotal: number }) {
  const threshold = STORE_POLICY.freeShippingThreshold;
  const free = subtotal > threshold;
  const remaining = threshold - subtotal;
  const pct = Math.min((subtotal / threshold) * 100, 100);
  return (
    <div data-free-shipping-meter className="mb-3 rounded-[4px] border border-(--line) bg-(--ground) p-3">
      <div className={`text-[12.5px] ${free ? "font-semibold text-(--ok)" : "text-(--ink-2)"}`}>
        {free ? (
          <>Free shipping unlocked ✓</>
        ) : remaining > 0 ? (
          <><span className="font-bold text-(--ink)">{formatMoney(remaining)}</span> away from free shipping</>
        ) : (
          <>Anything more ships free</>
        )}
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-(--card)">
        <div className={`h-full rounded-full transition-[width] duration-500 ease-out ${free ? "bg-(--ok)" : "bg-(--accent)"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** The listing behind a line; a variant's line borrows its family's brand and image. */
function asProduct(item: CartItem, catalog: Record<string, Product>): Product {
  const family = item.variant_of ? catalog[item.variant_of] : undefined;
  return (
    catalog[item.product_id] ?? {
      ...family,
      product_id: item.product_id,
      title: item.title,
      price: item.price,
      image_url: item.image_url ?? family?.image_url,
      options: undefined,
      option_values: item.option_values,
    }
  );
}

function lineName(item: CartItem): string {
  const chosen = optionValuesLabel(item);
  return chosen ? `${item.title} (${chosen})` : item.title;
}

export default function CartPanel({
  cart,
  checkoutStaged = false,
  onCartUpdate,
}: {
  cart: CartPayload | null;
  checkoutStaged?: boolean;
  onCartUpdate?: (cart: CartPayload) => void;
}) {
  const { ask } = useStoreFrame();
  const [cartBusy, setCartBusy] = useState(false);
  const [cartError, setCartError] = useState<string | null>(null);
  const items = cart?.items ?? [];
  const count = cart?.item_count ?? 0;
  const catalog = useCatalogIndex(fetchProducts);

  const applyCartChange = async (action: () => Promise<CartPayload | null>) => {
    if (cartBusy) return;
    setCartBusy(true);
    setCartError(null);
    const next = await action();
    if (next) onCartUpdate?.(next);
    else setCartError("The cart could not be updated. Please try again.");
    setCartBusy(false);
  };

  const beginCheckout = async () => {
    if (cartBusy) return;
    setCartBusy(true);
    setCartError(null);
    const current = await api.fetchCart<CartPayload>();
    if (!current) {
      setCartError("The cart could not be refreshed. Please try again.");
      setCartBusy(false);
      return;
    }
    onCartUpdate?.(current);
    setCartBusy(false);
    if (!current.items.length) {
      setCartError("Your cart is empty. Add an item before checking out.");
      return;
    }
    ask(
      "Stage a new checkout for the current cart. The cart may have changed after an earlier checkout, so re-read it and do not reuse any previous checkout summary.",
    );
  };

  const showCheckout = () => {
    const cards = document.querySelectorAll("[data-checkout-card]");
    const card = cards[cards.length - 1];
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
    else ask("Show me the checkout summary again.");
  };

  return (
    <BagPanel
      title="Your cart"
      count={plural(count, "item")}
      isEmpty={items.length === 0}
      empty={
        <div className="mx-auto max-w-[220px]">
          <span aria-hidden className="conto-mark-tile mx-auto mb-4 grid h-11 w-11 place-items-center"><ContoMark className="h-7 w-7" /></span>
          <strong className="block text-[14px] text-(--ink)">Your cart is ready</strong>
          <span className="mt-1 block">Ask the agent what you need. Conto checks the final cart before checkout.</span>
        </div>
      }
      footer={
        <>
          {items.length ? <FreeShippingMeter subtotal={cart?.subtotal ?? 0} /> : null}
          <TotalRow label={count ? `Subtotal · ${plural(count, "item")}` : "Subtotal"} value={formatMoney(cart?.subtotal ?? 0, cart?.currency)} />
          {items.length ? (
            <div className="checkout-assurance" aria-label="Conto checks merchant, spend, and approval rules">
              <span className="conto-mark-tile grid h-7 w-7 shrink-0 place-items-center" aria-hidden="true">
                <ContoMark className="h-[17px] w-[17px]" />
              </span>
              <span className="min-w-0">
                <strong>Checked by Conto</strong>
                <small>Merchant · spend · approval</small>
              </span>
            </div>
          ) : null}
          {checkoutStaged && items.length ? (
            <button
              type="button"
              onClick={showCheckout}
              disabled={cartBusy}
              className="mt-3 w-full rounded-(--radius) border border-(--line-strong) bg-(--card) py-2.5 text-[14px] font-semibold text-(--ink) transition hover:border-(--accent) disabled:opacity-50"
            >
              View summary
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void beginCheckout()}
              disabled={items.length === 0 || cartBusy}
              className="btn-primary mt-3 w-full disabled:opacity-50"
            >
              {cartBusy ? "Updating cart…" : "Check out"}
            </button>
          )}
          {cartError ? <p role="alert" className="mt-2 text-center text-[12px] text-(--danger)">{cartError}</p> : null}
          {items.length ? (
            <div className="mt-2.5 flex justify-center"><AskLink label="Ask the agent to review this cart" prompt="Look over my cart: anything missing or worth swapping?" /></div>
          ) : (
            <div className="mt-3 flex justify-center"><Pill tone="muted">Conto controls · Stripe test checkout</Pill></div>
          )}
        </>
      }
    >
      <ul className="divide-y divide-(--line)">
        {items.map((item) => {
          const product = asProduct(item, catalog);
          return (
            <li key={item.product_id} className={`ac-reveal flex gap-3 py-3.5 first:pt-0 ${cartBusy ? "opacity-60" : ""}`}>
              <ProductImage product={product} className="h-[70px] w-[70px] shrink-0 rounded-[4px] !text-3xl" />
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    {product.brand ? <div className="text-[10px] font-semibold uppercase tracking-[0.07em] text-(--ink-soft)">{product.brand}</div> : null}
                    <ProductTitle title={item.title} className="line-clamp-3 text-[13.5px] font-semibold leading-snug text-(--ink)" />
                    {optionValuesLabel(item) ? <div className="mt-0.5 text-[11.5px] text-(--ink-soft)">{optionValuesLabel(item)}</div> : null}
                  </div>
                  <div className="shrink-0 text-right">
                    <div className="text-[14px] font-bold tabular-nums text-(--ink)">{formatMoney(item.line_total)}</div>
                    {item.quantity > 1 ? <div className="text-[11px] text-(--ink-soft)">{formatMoney(item.price)} each</div> : null}
                  </div>
                </div>
                <DeliveryPromise product={product} className="mt-0.5" />
                <div className="mt-2 flex items-center gap-2.5">
                  <Stepper
                    quantity={item.quantity}
                    itemTitle={lineName(item)}
                    onChange={(quantity) => void applyCartChange(() => quantity < 1
                      ? removeCartItem(item.product_id)
                      : updateCartItem(item.product_id, quantity))}
                  />
                  <RemoveLink
                    itemTitle={lineName(item)}
                    onClick={() => void applyCartChange(() => removeCartItem(item.product_id))}
                  />
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </BagPanel>
  );
}

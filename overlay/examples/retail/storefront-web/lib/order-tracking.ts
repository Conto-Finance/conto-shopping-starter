// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { Order } from "web-shared";

export const ORDER_TRACKING_EVENT = "anthropic-shopping:order-tracking";

export interface OrderTrackingEventDetail {
  order: Order;
}

export function openOrderTracking(order: Order): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<OrderTrackingEventDetail>(ORDER_TRACKING_EVENT, {
      detail: { order },
    }),
  );
}

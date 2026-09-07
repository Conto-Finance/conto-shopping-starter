// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useMemo } from "react";
import type { MemoryFact, TraceEntry } from "./protocol";

interface ActivityRow {
  tool: string;
  status: "running" | "blocked" | "error" | "done";
}

const TOOL_LABELS: Record<string, string> = {
  search_products: "Searched the catalog",
  get_product: "Checked product details",
  get_cart: "Reviewed the cart",
  add_to_cart: "Added an item to the cart",
  update_cart_item: "Updated the cart",
  remove_from_cart: "Removed an item from the cart",
  get_fulfillment_options: "Checked delivery options",
  search_policies: "Checked store policies",
  checkout: "Checked the cart for payment",
  remember: "Saved a shopping preference",
  recall: "Recalled shopping preferences",
};

function activityLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool.replaceAll("_", " ").replace(/^\w/, (letter) => letter.toUpperCase());
}

function activityRows(entries: TraceEntry[]): ActivityRow[] {
  const rows: ActivityRow[] = [];
  const open = new Map<string, ActivityRow[]>();
  for (const entry of entries) {
    if (entry.kind === "tool_call") {
      if (entry.label.startsWith("present_")) continue;
      const row: ActivityRow = { tool: entry.label, status: "running" };
      rows.push(row);
      open.set(entry.label, [...(open.get(entry.label) ?? []), row]);
    } else if (entry.kind === "tool_result") {
      const row = open.get(entry.label)?.shift();
      if (!row) continue;
      row.status =
        entry.status === "blocked" ? "blocked" : entry.isError ? "error" : "done";
    }
  }
  return rows;
}

const STATUS_COPY: Record<ActivityRow["status"], string> = {
  running: "In progress",
  blocked: "Stopped by a safety check",
  error: "Needs attention",
  done: "Done",
};

const STATUS_TONE: Record<ActivityRow["status"], string> = {
  running: "bg-(--well) text-(--ink-soft)",
  blocked: "bg-(--warn-soft) text-(--warn)",
  error: "bg-(--danger-soft) text-(--danger)",
  done: "bg-(--ok-soft) text-(--ok)",
};

export function Inspector({
  turnCount,
  streaming,
  trace,
  memory,
  newMemoryKeys,
  memoryTitle = "Saved preferences",
  onClose,
}: {
  turnCount: number;
  streaming: boolean;
  trace: TraceEntry[];
  memory: MemoryFact[];
  newMemoryKeys: ReadonlySet<string>;
  memoryTitle?: string;
  onClose: () => void;
}) {
  const entries = useMemo(
    () => trace.filter((entry) => entry.turn === turnCount),
    [trace, turnCount],
  );
  const rows = useMemo(() => activityRows(entries), [entries]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div onClick={onClose} aria-hidden className="fixed inset-0 z-40 bg-black/30" />
      <aside className="fixed inset-y-0 right-0 z-50 flex w-[min(94vw,400px)] flex-col border-l border-(--line) bg-(--card) shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-(--line) px-4 py-3">
          <h2 className="text-sm font-bold text-(--ink)">Activity</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close activity"
            className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
          >
            ×
          </button>
        </div>

        <div className="panel-scroll flex-1 overflow-y-auto px-4 py-4">
          <section>
            <h3 className="text-[13px] font-semibold text-(--ink)">Latest shopping activity</h3>
            {rows.length === 0 ? (
              <p className="mt-1 text-[13px] text-(--ink-soft)">
                {streaming ? "Working…" : "No activity yet."}
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-(--line)">
                {rows.map((row, index) => (
                  <li key={`${row.tool}-${index}`} className="flex items-center gap-3 py-2.5">
                    <span
                      aria-hidden
                      className={`h-2 w-2 shrink-0 rounded-full ${STATUS_TONE[row.status]}`}
                    />
                    <span className="min-w-0 flex-1 text-[13px] text-(--ink)">
                      {activityLabel(row.tool)}
                    </span>
                    <span className="shrink-0 text-[11px] text-(--ink-soft)">
                      {STATUS_COPY[row.status]}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="mt-5 border-t border-(--line) pt-4">
            <h3 className="text-[13px] font-semibold text-(--ink)">{memoryTitle}</h3>
            {memory.length === 0 ? (
              <p className="mt-1 text-[13px] text-(--ink-soft)">Nothing saved yet.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {memory.map((fact) => (
                  <li key={fact.key} className="text-[13px] leading-snug text-(--ink)">
                    {fact.value}
                    {newMemoryKeys.has(fact.key) ? (
                      <span className="ml-1.5 text-(--ink-soft)">New</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </aside>
    </>
  );
}

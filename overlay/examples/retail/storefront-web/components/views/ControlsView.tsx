// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";
import { formatMoney, KindIcon, PageHeader, Panel, Pill, StorePage } from "web-shared";
import { updateContoControls, type ContoControlUpdate, type ContoControls } from "@/lib/api";

type SpendLimitKey =
  | "maxTransaction"
  | "approvalThreshold"
  | "dailyLimit"
  | "weeklyLimit"
  | "monthlyLimit";

type ControlDraft = Omit<ContoControlUpdate, SpendLimitKey> &
  Record<SpendLimitKey, string>;

const SPEND_LIMIT_KEYS: SpendLimitKey[] = [
  "maxTransaction",
  "approvalThreshold",
  "dailyLimit",
  "weeklyLimit",
  "monthlyLimit",
];

function money(value: number | null, currency = "USD"): string {
  return value == null ? "Not set" : formatMoney(value, currency);
}

function validateDraft(
  draft: ControlDraft,
  ceilings: ContoControls["spend"]["controlCeilings"],
  currency: string,
): string | null {
  const parsed = parseDraft(draft);
  if (!parsed) return "Enter a positive amount for every spend control.";
  const values = SPEND_LIMIT_KEYS.map((key) => parsed[key]);
  if (values.some((value) => !Number.isFinite(value) || value <= 0)) {
    return "Enter a positive amount for every spend control.";
  }
  if (parsed.approvalThreshold > parsed.maxTransaction) {
    return `Approval threshold must be at or below the ${money(parsed.maxTransaction, currency)} per-purchase limit. Raise Per purchase first.`;
  }
  if (parsed.maxTransaction > parsed.dailyLimit) {
    return "Daily limit must be at least as high as the per-purchase limit.";
  }
  if (parsed.dailyLimit > parsed.weeklyLimit) {
    return "Weekly limit must be at least as high as the daily limit.";
  }
  if (parsed.weeklyLimit > parsed.monthlyLimit) {
    return "Monthly limit must be at least as high as the weekly limit.";
  }

  const ceilingChecks: Array<[string, number, number | null]> = [
    ["Per-purchase", parsed.maxTransaction, ceilings.maxTransaction],
    ["Approval threshold", parsed.approvalThreshold, ceilings.approvalThreshold],
    ["Daily", parsed.dailyLimit, ceilings.dailyLimit],
    ["Weekly", parsed.weeklyLimit, ceilings.weeklyLimit],
    ["Monthly", parsed.monthlyLimit, ceilings.monthlyLimit],
  ];
  const exceeded = ceilingChecks.find(([, value, ceiling]) => ceiling != null && value > ceiling);
  if (exceeded) {
    return `${exceeded[0]} cannot exceed the ${money(exceeded[2], currency)} effective limit.`;
  }
  return null;
}

function LimitInput({ label, value, max, onChange, note, featured = false }: {
  label: string;
  value: string;
  max: number | null;
  onChange: (value: string) => void;
  note: string;
  featured?: boolean;
}) {
  return (
    <label className={`limit-card border border-(--line) bg-(--card) p-4 ${featured ? "limit-card-featured" : ""}`}>
      <span className="brand-meta flex items-center justify-between gap-2">
                {label}
        {featured ? <span className="text-(--accent-ink)">Checkout rule</span> : null}
      </span>
      <span className="limit-input-shell mt-2.5 flex items-center gap-1.5 border border-(--line-strong) bg-(--ground) px-3 py-2 transition">
        <span className="text-[14px] font-semibold text-(--ink-soft)">$</span>
        <input
          type="number"
          min="1"
          max={max ?? undefined}
          step="1"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-[22px] font-semibold tracking-[-0.02em] tabular-nums text-(--ink) outline-none"
        />
      </span>
      <span className="mt-2 block text-[11.5px] leading-snug text-(--ink-soft)">{note}</span>
    </label>
  );
}

function Toggle({ checked, onChange, label, note }: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  note: string;
}) {
  return (
    <label className="group flex cursor-pointer items-center justify-between gap-4 rounded-[4px] border border-(--line) bg-(--card) p-4 transition hover:border-(--line-strong)">
      <span>
        <span className="flex items-center gap-2 text-[14px] font-semibold text-(--ink)"><i aria-hidden className={`h-2 w-2 rounded-full ${checked ? "bg-(--ok)" : "bg-(--warn)"}`} />{label}</span>
        <span className="mt-1 block text-[12px] leading-snug text-(--ink-soft)">{note}</span>
      </span>
      <span className={`relative h-6 w-11 shrink-0 rounded-full transition ${checked ? "bg-(--ok)" : "bg-(--line-strong)"}`}>
        <input className="sr-only" type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
        <span className={`absolute top-1 h-4 w-4 rounded-full bg-(--ground) transition ${checked ? "left-6" : "left-1"}`} />
      </span>
    </label>
  );
}

function ManageLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-[4px] border border-(--line-strong) bg-(--card) px-3 py-2 text-[12.5px] font-semibold text-(--ink) transition hover:border-(--accent-strong) hover:bg-(--accent-soft)">
      {children}<span aria-hidden>↗</span>
    </a>
  );
}

function initialDraft(controls: ContoControls): ControlDraft {
  return {
    maxTransaction: String(controls.spend.maxTransaction ?? 100),
    approvalThreshold: String(controls.spend.approvalThreshold ?? 50),
    dailyLimit: String(controls.spend.dailyLimit ?? 500),
    weeklyLimit: String(controls.spend.weeklyLimit ?? 2000),
    monthlyLimit: String(controls.spend.monthlyLimit ?? 5000),
    merchantAllowed: controls.merchants.current.allowed,
    agentActive: controls.agent.status === "ACTIVE",
  };
}

function parseDraft(draft: ControlDraft): ContoControlUpdate | null {
  const parsedLimits = Object.fromEntries(
    SPEND_LIMIT_KEYS.map((key) => [key, Number(draft[key])]),
  ) as Record<SpendLimitKey, number>;
  if (
    SPEND_LIMIT_KEYS.some(
      (key) => draft[key].trim() === "" || !Number.isFinite(parsedLimits[key]),
    )
  ) {
    return null;
  }
  return {
    ...parsedLimits,
    merchantAllowed: draft.merchantAllowed,
    agentActive: draft.agentActive,
  };
}

export default function ControlsView({ controls, failed, onUpdated }: {
  controls: ContoControls | null;
  failed: boolean;
  onUpdated: (controls: ContoControls) => void;
}) {
  const [draft, setDraft] = useState<ControlDraft | null>(controls ? initialDraft(controls) : null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (controls) setDraft(initialDraft(controls));
  }, [controls]);

  if (!controls || !draft) {
    return (
      <StorePage>
        <PageHeader title="Agent controls" subtitle="Live policy, spend, merchant access, and checkout controls." />
        <Panel><div className="p-5 text-[14px] text-(--ink-soft)">{failed ? "Controls are temporarily unavailable. Checkout will fail closed." : "Loading shopping controls…"}</div></Panel>
      </StorePage>
    );
  }

  const set = <K extends keyof ControlDraft>(key: K, value: ControlDraft[K]) => {
    setDraft((current) => current ? { ...current, [key]: value } : current);
    setSaveState("idle");
    setSaveError(null);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    const update = parseDraft(draft);
    const validationError = validateDraft(
      draft,
      controls.spend.controlCeilings,
      controls.spend.currency,
    );
    if (!update || validationError) {
      setSaveError(validationError ?? "Enter a positive amount for every spend control.");
      setSaveState("error");
      return;
    }
    setSaveState("saving");
    setSaveError(null);
    const result = await updateContoControls(update);
    if (!result.controls) {
      setSaveError(result.error);
      setSaveState("error");
      return;
    }
    onUpdated(result.controls);
    setSaveState("saved");
  };

  const { currency, controlCeilings } = controls.spend;
  const todayLimit = Number(draft.dailyLimit) || 1;
  const todayPct = Math.min(100, (controls.spend.spent.today / todayLimit) * 100);
  const typedMaxTransaction = Number(draft.maxTransaction);
  const approvalMaximum = Number.isFinite(typedMaxTransaction) && typedMaxTransaction > 0
    ? controlCeilings.approvalThreshold == null
      ? typedMaxTransaction
      : Math.min(typedMaxTransaction, controlCeilings.approvalThreshold)
    : controlCeilings.approvalThreshold;
  const draftError = validateDraft(draft, controlCeilings, currency);
  return (
    <StorePage>
      <PageHeader title="Agent controls" subtitle="Spend policy, counterparty access, and checkout controls for this shopping session.">
        <Pill tone={controls.agent.status === "ACTIVE" ? "ok" : "warn"} dot>{controls.agent.status === "ACTIVE" ? "Controls active" : "Agent paused"}</Pill>
      </PageHeader>

      <section className="control-overview border border-(--line)">
        <div className="p-5 sm:p-6">
          <h2 className="max-w-[560px] text-[22px] font-light leading-tight tracking-[-0.025em] text-(--ink) sm:text-[25px]">Controls for this shopping agent</h2>
          <p className="mt-2 max-w-[610px] text-[13.5px] leading-relaxed text-(--ink-2)">Set where the agent can spend, which merchants it can pay, and when a person must approve. Saved changes apply to the next checkout.</p>
        </div>
        <div className="control-summary-grid">
          <div><span>Spent today</span><strong>{money(controls.spend.spent.today, currency)}</strong></div>
          <div><span>Available today</span><strong>{money(controls.spend.remaining.today, currency)}</strong></div>
          <div><span>Merchant</span><strong>{controls.merchants.current.allowed ? "Allowed" : "Blocked"}</strong></div>
          <div><span>Checkout</span><strong>Stripe test mode</strong></div>
        </div>
      </section>

      <form onSubmit={save} noValidate className="space-y-4">
        <Panel title="Spend limits" subtitle="Editable policy rules, enforced in real time" icon={<KindIcon icon="chart" tone="accent" size={36} />}>
          <div className="mx-[18px] mt-2 rounded-[4px] border border-(--line) bg-(--ground) px-4 py-3">
            <div className="flex items-center justify-between gap-3 text-[11.5px]">
              <span className="font-medium text-(--ink-soft)">Today’s usage</span>
              <span className="font-semibold tabular-nums text-(--ink)">{money(controls.spend.spent.today, currency)} of {money(Number(draft.dailyLimit) || null, currency)}</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-(--well)"><div className="h-full rounded-full bg-(--accent) transition-[width] duration-500" style={{ width: `${todayPct}%` }} /></div>
          </div>
          <div className="grid gap-3 p-[18px] pt-3 sm:grid-cols-2 lg:grid-cols-3">
            <LimitInput featured label="Per purchase" value={draft.maxTransaction} max={controlCeilings.maxTransaction} onChange={(value) => set("maxTransaction", value)} note={`Adjustable up to ${money(controlCeilings.maxTransaction, currency)}`} />
            <LimitInput featured label="Approval over" value={draft.approvalThreshold} max={approvalMaximum} onChange={(value) => set("approvalThreshold", value)} note={controlCeilings.approvalThreshold == null ? "Higher purchases wait for human review." : `Higher purchases wait for human review. Effective ceiling ${money(controlCeilings.approvalThreshold, currency)}.`} />
            <LimitInput label="Daily" value={draft.dailyLimit} max={controlCeilings.dailyLimit} onChange={(value) => set("dailyLimit", value)} note={`${money(controls.spend.remaining.today, currency)} available today`} />
            <LimitInput label="Weekly" value={draft.weeklyLimit} max={controlCeilings.weeklyLimit} onChange={(value) => set("weeklyLimit", value)} note={`${money(controls.spend.remaining.thisWeek, currency)} available this week`} />
            <LimitInput label="Monthly" value={draft.monthlyLimit} max={controlCeilings.monthlyLimit} onChange={(value) => set("monthlyLimit", value)} note={`${money(controls.spend.remaining.thisMonth, currency)} available this month`} />
            <div className="limit-card rounded-[4px] border border-(--line) bg-(--card) p-4">
              <p className="brand-meta">Checkout usage today</p>
              <p className="mt-2.5 text-[22px] font-semibold tracking-[-0.03em] text-(--ink)">{money(controls.spend.spent.today, currency)}</p>
              <div className="mt-2 flex flex-wrap gap-1.5"><Pill>Card + Link {money(controls.checkout.spent.today, currency)}</Pill></div>
            </div>
          </div>
        </Panel>

        <Panel title="Access boundaries" subtitle="Counterparty allowlist and agent kill switch" icon={<KindIcon icon="tag" tone={draft.merchantAllowed && draft.agentActive ? "ok" : "warn"} size={36} />} action={<ManageLink href={controls.manage.merchants}>View counterparties</ManageLink>}>
          <div className="grid gap-3 p-[18px] pt-3 sm:grid-cols-2">
            <Toggle checked={draft.merchantAllowed} onChange={(value) => set("merchantAllowed", value)} label={`Allow ${controls.merchants.current.name}`} note={controls.merchants.current.registered ? "Registered merchant" : "Address-bound allowlist entry"} />
            <Toggle checked={draft.agentActive} onChange={(value) => set("agentActive", value)} label="Shopping agent active" note="Turn off to pause all checkout attempts." />
          </div>
        </Panel>

        <Panel title="Checkout handoff" subtitle="Anthropic hands the approved cart to the merchant checkout" icon={<KindIcon icon="check" tone="info" size={36} />}>
          <div className="p-[18px] pt-3">
            <div className="checkout-method-card rounded-[4px] border border-(--line) p-4"><div className="flex items-center justify-between gap-2"><p className="text-[14px] font-semibold text-(--ink)">{controls.checkout.label}</p><Pill tone="info">Test mode</Pill></div><p className="mt-2 text-[12.5px] leading-relaxed text-(--ink-soft)">The exact cart is authorized before Stripe opens; shoppers can enter a test card or use Link.</p></div>
          </div>
        </Panel>

        <div className="save-dock sticky bottom-3 flex flex-wrap items-center justify-between gap-3 rounded-[4px] border border-(--line-strong) bg-(--card) p-3.5 sm:p-4">
          <div>
            <p className="flex items-center gap-2 text-[14px] font-semibold text-(--ink)"><i aria-hidden className={`h-2 w-2 rounded-full ${saveState === "error" ? "bg-(--danger)" : saveState === "saved" ? "bg-(--ok)" : "bg-(--ink-faint)"}`} />{saveState === "saved" ? "Changes applied" : "Session controls"}</p>
            <p role={saveState === "error" || draftError ? "alert" : undefined} className={`mt-0.5 text-[12px] ${saveState === "error" || draftError ? "text-red-700" : "text-(--ink-soft)"}`}>{saveError ?? draftError ?? "Purchase ≤ daily ≤ weekly ≤ monthly; stricter organization and wallet controls still apply."}</p>
          </div>
          <button type="submit" disabled={saveState === "saving"} className="rounded-[4px] bg-(--ink) px-4 py-2.5 text-[13px] font-semibold text-(--surface) transition hover:brightness-110 disabled:opacity-50">{saveState === "saving" ? "Applying…" : "Apply to agent"}</button>
        </div>
      </form>
    </StorePage>
  );
}

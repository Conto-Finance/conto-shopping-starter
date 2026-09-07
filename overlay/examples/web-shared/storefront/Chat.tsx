// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { type ReactNode, useMemo } from "react";
import { useStickToBottom } from "../scroll";
import { LatestPill, Transcript, type TranscriptProps } from "../Transcript";
import type { AgentTurn } from "../turn";

/** The storefronts' conversation page: the home until the first message, then the transcript. */
export function Chat({
  chat,
  home,
  footer,
  renderBlock,
  renderPending,
  wide,
}: {
  chat: AgentTurn;
  home: ReactNode;
  /** Durable events, such as a completed order, shown after the current transcript. */
  footer?: ReactNode;
  renderBlock: TranscriptProps["renderBlock"];
  renderPending?: TranscriptProps["renderPending"];
  /** Components that may extend past the text measure when the page has room. */
  wide?: ReadonlySet<string>;
}) {
  const hasFooter = footer != null;
  const scrollItems = useMemo(
    () => (hasFooter ? [...chat.items, "footer"] : chat.items),
    [chat.items, hasFooter],
  );
  const { scrollRef, onScroll, showLatest, jumpToLatest } = useStickToBottom(
    scrollItems,
    chat.busy,
  );
  return (
    <div className="relative h-full">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="panel-scroll h-full overflow-y-auto px-4 pb-8 pt-6 sm:px-6"
      >
        <div className="mx-auto flex max-w-[760px] flex-col gap-5 text-[15.5px]">
          {chat.items.length === 0 ? (
            home
          ) : (
            <Transcript
              items={chat.items}
              busy={chat.busy}
              send={chat.send}
              renderBlock={renderBlock}
              renderPending={renderPending}
              wide={wide}
            />
          )}
          {footer}
        </div>
      </div>
      {showLatest ? <LatestPill onClick={jumpToLatest} /> : null}
    </div>
  );
}

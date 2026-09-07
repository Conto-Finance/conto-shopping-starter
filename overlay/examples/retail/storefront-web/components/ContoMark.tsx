// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

export default function ContoMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="-100 -100 200 200"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <g transform="rotate(180)">
        <rect x="-80" y="-70" width="120" height="36" fill="currentColor" />
        <rect x="-40" y="-18" width="120" height="36" fill="currentColor" />
        <rect x="-80" y="34" width="120" height="36" fill="currentColor" />
      </g>
    </svg>
  );
}

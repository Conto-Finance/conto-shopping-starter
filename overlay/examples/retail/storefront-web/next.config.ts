// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0
// Conto modification: standalone shopping storefront served at the root URL.
import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["web-shared"],
};
export default nextConfig;

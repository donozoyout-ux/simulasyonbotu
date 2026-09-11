import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  reactStrictMode: true,
  turbopack: { root: process.cwd() },
};

export default nextConfig;

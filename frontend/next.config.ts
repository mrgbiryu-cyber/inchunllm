import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  transpilePackages: ['three', 'react-force-graph-3d', '3d-force-graph', 'three-forcegraph', 'three-render-objects'],
  allowedDevOrigins: ['100.77.67.1'],
  outputFileTracingRoot: __dirname,
  turbopack: {
    root: __dirname,
  },
  webpack: (config) => {
    config.resolve ?? (config.resolve = {});
    config.resolve.alias = {
      ...(config.resolve.alias || {}),
      tailwindcss: path.resolve(__dirname, "node_modules/tailwindcss"),
    };
    return config;
  },
};

export default nextConfig;

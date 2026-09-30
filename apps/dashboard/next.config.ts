import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["maplibre-gl"],
  // Middleware verifies HS256 with process.env.JWT_SECRET at request time.
  // That value must be the same secret the API signs with. It is not copied
  // into `env`, which would inline the secret into the browser bundle.
};

export default nextConfig;

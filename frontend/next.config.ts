import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/:path*",
      },
      {
        source: "/socket.io",
        destination: "http://127.0.0.1:8000/socket.io/",
      },
    ]
  },
}

export default nextConfig

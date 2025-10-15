/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'https://poker-assistant.onrender.com/:path*',
      },
    ];
  },
};
module.exports = nextConfig;

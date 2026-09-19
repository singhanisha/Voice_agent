/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Dynamically route based on the API URL provided in the environment
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    // Remove inline comments (e.g., #) if present in the env var
    let cleanApiUrl = apiUrl.split('#')[0].trim();
    
    // Sanitize trailing slashes to prevent double-slashes in proxied paths
    if (cleanApiUrl.endsWith('/')) {
      cleanApiUrl = cleanApiUrl.slice(0, -1);
    }
    
    return [
      {
        source: '/api/:path*',
        destination: `${cleanApiUrl}/api/:path*` // Proxy to Backend
      },
      {
        source: '/ws/:path*',
        destination: `${cleanApiUrl}/ws/:path*` // Proxy Websockets
      }
    ];
  }
};

export default nextConfig;

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          black: 'var(--color-black)',
          white: 'var(--color-white)',
          cream: 'var(--color-cream)',
          muted: 'var(--color-text-muted)',
          faint: 'var(--color-text-faint)',
          border: 'var(--color-border)',
        }
      },
      borderRadius: {
        'pill': 'var(--radius-pill)',
        'card': 'var(--radius-card)',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'sans-serif'],
        mono: ['var(--font-mono)', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      }
    },
  },
  plugins: [],
}

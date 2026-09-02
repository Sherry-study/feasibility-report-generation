/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: { DEFAULT: 'var(--bg)', 2: 'var(--bg2)', 3: 'var(--bg3)' },
        text: { DEFAULT: 'var(--text)', 2: 'var(--text2)', 3: 'var(--text3)' },
        border: { DEFAULT: 'var(--border)', 2: 'var(--border2)' },
        accent: { DEFAULT: 'var(--accent)', 2: 'var(--accent2)', 3: 'var(--accent3)' },
        green: 'var(--green)',
        orange: 'var(--orange)',
        red: 'var(--red)',
        purple: 'var(--purple)',
        cyan: 'var(--cyan)',
        yellow: 'var(--yellow)',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'SF Pro Display', 'SF Pro Text', 'Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['SF Mono', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        DEFAULT: 'var(--radius)',
        lg: 'var(--radius-lg)',
        pill: 'var(--radius-pill)',
      },
      boxShadow: {
        card: 'var(--shadow)',
        popover: 'var(--shadow-popover)',
      },
      animation: {
        breathe: 'breathe 2.5s ease-in-out infinite',
        'dot-bounce': 'dot-bounce 1.4s ease-in-out infinite',
        'fade-in': 'fade-in 0.3s ease-out',
      },
      spacing: {
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
      },
    },
  },
  plugins: [],
};

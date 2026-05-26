/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        sig: {
          bg: '#f3f5f8',
          paper: '#ffffff',
          ink: '#1f2937',
          muted: '#6b7280',
          border: '#e5e7eb',
          accent: '#1e40af',
          'accent-soft': '#dbeafe',
        },
      },
    },
  },
  plugins: [],
};

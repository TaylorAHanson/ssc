/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(214.3 31.8% 91.4%)",
        primary: {
          DEFAULT: "var(--brand-primary)",
        },
        secondary: "var(--brand-secondary)",
        info: "var(--brand-info)",
        alert: "var(--brand-alert)",
        warning: "var(--brand-warning)",
        success: "var(--brand-success)",
        background: {
          DEFAULT: "var(--theme-surface)",
        },

        // Shared design tokens — see src/theme.ts.
        // Use these instead of one-off hex values so the look stays
        // consistent and remains in sync with the Command Center app.
        nav: {
          DEFAULT: "var(--theme-nav-bg)",
          bg: "var(--theme-nav-bg)",
          border: "var(--theme-nav-border)",
          text: "var(--theme-nav-text)",
          "text-muted": "var(--theme-nav-text-muted)",
          hover: "var(--theme-nav-hover-bg)",
          active: "var(--theme-nav-active-bg)",
          "active-text": "var(--theme-nav-active-text)",
        },
        heading: "var(--theme-heading)",
        accent: {
          DEFAULT: "var(--theme-accent)",
          soft: "var(--theme-accent-soft)",
        },
        surface: {
          DEFAULT: "var(--theme-surface)",
          muted: "var(--theme-surface-muted)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
}

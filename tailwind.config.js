/** @type {import('tailwindcss').Config} */
import typography from "@tailwindcss/typography";

const withAlpha = (cssVar) =>
  `color-mix(in srgb, var(${cssVar}) calc(<alpha-value> * 100%), transparent)`;

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(214.3 31.8% 91.4%)",
        // Brand colors are runtime CSS variables (admin-editable hex), so
        // opacity modifiers like `bg-primary/10` need color-mix — a bare
        // var() silently drops them.
        primary: {
          DEFAULT: withAlpha("--brand-primary"),
        },
        secondary: withAlpha("--brand-secondary"),
        info: withAlpha("--brand-info"),
        alert: withAlpha("--brand-alert"),
        warning: withAlpha("--brand-warning"),
        success: withAlpha("--brand-success"),
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
  plugins: [
    // Adds first-class styles for `prose` / `prose-sm` so agent
    // messages rendered from markdown (headings, tables, code blocks,
    // blockquotes, lists, etc.) get sensible default typography
    // without us hand-rolling each rule.
    typography,
  ],
}

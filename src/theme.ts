/**
 * Shared design tokens.
 *
 * This file is the single source of truth for the visual language used across
 * this app. It mirrors the look used by the companion Command Center app, so
 * the two products feel like a single suite.
 *
 * Token naming is intentionally semantic (`nav`, `accent`, `surface`, ...) so
 * this file can be copy-pasted into the Command Center repo without rename.
 *
 * Most of these tokens are also exposed as CSS variables in `index.css` and
 * as Tailwind utilities in `tailwind.config.js`. Prefer the Tailwind utility
 * classes (`bg-nav`, `text-nav-muted`, `text-heading`, ...) when styling
 * components. Import from this file only when you need a raw hex value in
 * inline styles or canvas/chart libraries.
 */

export const theme = {
  colors: {
    // Dark navigation surface (sidebar / vertical nav).
    nav: {
      bg: '#001E3C',
      border: '#1F2937', // ~ tailwind gray-800
      text: '#FFFFFF',
      textMuted: '#9CA3AF', // ~ tailwind gray-400
      hoverBg: '#1F2937',
      activeBg: '#007BFF',
      activeText: '#FFFFFF',
    },

    // Headings used in light surfaces (top bar titles, page H1s).
    heading: '#001E3C',

    // Primary accent. Drives buttons, links, badges, active highlights.
    accent: '#007BFF',
    accentSoft: '#EBF4FF',

    // App-level light surfaces.
    surface: '#F8F9FA',
    surfaceMuted: '#F3F4F6',
  },
} as const;

export type Theme = typeof theme;

/** @type {import('tailwindcss').Config} */
// Precompiled Tailwind config (U24.9). Rebuild with `make css` after adding
// new utility classes to templates or app/static/js.
module.exports = {
  future: {
    // UX3b: emit hover:/group-hover: variants inside @media (hover: hover) and
    // (pointer: fine) so touch devices never activate hover-revealed overlays
    // (browsers emulate :hover during a tap -> invisible buttons become live).
    hoverOnlyWhenSupported: true,
  },
  content: [
    "./app/**/templates/**/*.html",
    "./app/static/js/**/*.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Manrope", "ui-sans-serif", "system-ui"],
      },
    },
  },
  plugins: [],
};

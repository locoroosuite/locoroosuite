/** @type {import('tailwindcss').Config} */
// Precompiled Tailwind config (U24.9). Rebuild with `make css` after adding
// new utility classes to templates or app/static/js.
module.exports = {
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

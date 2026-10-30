/** Tailwind CSS v3 config. Compiled output lives in app/static/css/app.css (the CSP forbids CDN styles). */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"] },
      colors: { brand: { 50: "#eef2ff", 100: "#e0e7ff", 200: "#c7d2fe", 300: "#a5b4fc", 400: "#818cf8", 500: "#6366f1", 600: "#4f46e5", 700: "#4338ca", 800: "#3730a3", 900: "#312e81" } },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};

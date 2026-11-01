/** @type {import('tailwindcss').Config} */
// Monochrome palette: ink #0a0a0a on #f5f5f5, white cards, black/white pills.
// Accents are used only for meaning: green = good, cyan = info, orange = caution, red = problem.
// Existing class names keep working; their colours are remapped:
//   slate/ink + violet/brand -> neutral greys, emerald/sage -> green, sky/mist -> cyan,
//   amber/sand -> orange, rose/red/orange/coral -> red.
const ink = {
  50: "#fafafa", 100: "#f5f5f5", 200: "#e8e8e8", 300: "#cecece", 400: "#6e6e6e", 500: "#6b6b6b",
  600: "#4a4a4a", 700: "#313131", 800: "#181818", 900: "#0a0a0a", 950: "#050505",
};
const green = {
  50: "#f0f9ef", 100: "#dcf1da", 200: "#b9e2b5", 300: "#8fcf89", 400: "#5fb160", 500: "#40a53c",
  600: "#33872f", 700: "#2a6b27", 800: "#245522", 900: "#1f461d", 950: "#0d260c",
};
const cyan = {
  50: "#ebfbff", 100: "#cdf4ff", 200: "#a0eaff", 300: "#5edcff", 400: "#1ccaff", 500: "#00bbff",
  600: "#0096d1", 700: "#0077a8", 800: "#06628a", 900: "#0b5272", 950: "#08344c",
};
const orange = {
  50: "#fff8ed", 100: "#feefd4", 200: "#fcdba8", 300: "#fac070", 400: "#f9a83f", 500: "#f7931e",
  600: "#db7612", 700: "#b55911", 800: "#924615", 900: "#773b15", 950: "#401c08",
};
const red = {
  50: "#fef2f2", 100: "#fee2e2", 200: "#fecaca", 300: "#fca5a5", 400: "#f87171", 500: "#ef4444",
  600: "#dc2626", 700: "#b91c1c", 800: "#991b1b", 900: "#7f1d1d", 950: "#450a0a",
};

export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        slate: ink, violet: ink, emerald: green, sky: cyan, amber: orange, rose: red, red, orange: red,
        ink, brand: ink, sage: green, mist: cyan, sand: orange, coral: red, page: "#f5f5f5",
      },
      fontFamily: {
        // Inter for Latin; Noto Sans Devanagari (loaded) for Hindi/Marathi; system Noto/Indic fonts for other scripts.
        sans: ["Inter", "Noto Sans Devanagari", "Noto Sans", "Nirmala UI", "Mangal", "system-ui", "sans-serif"],
        display: ["Inter", "Noto Sans Devanagari", "Noto Sans", "Nirmala UI", "system-ui", "sans-serif"],
      },
      letterSpacing: { tightest: "-0.055em", tighter: "-0.04em", snug: "-0.02em" },
      boxShadow: {
        soft: "0 1px 2px rgba(0,0,0,.04)",
        lift: "0 12px 40px -16px rgba(0,0,0,.22)",
        btn: "0 2px 8px #00000026",
      },
      borderRadius: { card: "25px", btn: "9999px", field: "18px", box: "14px" },
      transitionTimingFunction: { smooth: "cubic-bezier(.4,0,.2,1)" },
      keyframes: {
        "fade-up": { from: { opacity: 0, transform: "translateY(1.5rem)" }, to: { opacity: 1, transform: "none" } },
        marquee: { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
      },
      animation: {
        "fade-up": "fade-up .5s cubic-bezier(.4,0,.2,1) both",
        marquee: "marquee 40s linear infinite",
      },
    },
  },
  plugins: [],
};

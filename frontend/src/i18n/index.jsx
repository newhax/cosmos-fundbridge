// UI language: English and Hindi have string files; the other configured languages are listed and fall back
// to English until their file exists. The choice is saved per user (server) and in this browser.
import { createContext, useContext, useMemo, useState } from "react";
import en from "./en.js";
import hi from "./hi.js";

export const DICTS = { en, hi };
const Ctx = createContext({ lang: "en", setLang: () => {}, t: (k) => en[k] || k });

function stored() {
  try { return localStorage.getItem("fb_lang") || "en"; } catch { return "en"; }
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(stored);
  const value = useMemo(() => {
    const dict = DICTS[lang] || en;
    const t = (key, vars) => {
      let s = dict[key] ?? en[key] ?? key;
      for (const [k, v] of Object.entries(vars || {})) s = s.replace(`{${k}}`, v);
      return s;
    };
    const setLang = (l) => {
      setLangState(l);
      try { localStorage.setItem("fb_lang", l); } catch { /* private mode */ }
      document.documentElement.lang = l;
    };
    return { lang, setLang, t, translated: Boolean(DICTS[lang]) };
  }, [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);

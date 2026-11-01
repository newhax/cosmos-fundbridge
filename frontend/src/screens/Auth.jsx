import { useEffect, useState } from "react";
import { api, inr } from "../api.js";
import { Err, Ok } from "../components/ui.jsx";

const ROLES = [
  ["ngo", "NGO", "Find CSR funders, pitch them, track follow-ups."],
  ["company", "Company (CSR team)", "Receive and screen pitches from NGOs."],
  ["donor", "Individual donor", "Discover NGOs you can give to directly. India / INR only."],
];

export const PRIVACY_NOTICE = [
  "FundBridge is a demo. Do not enter confidential data.",
  "We store your email, a password hash and the profile fields you choose. Individual donors: only a display name, state, city and preferences.",
  "AI features send public CSR data and your profile text to Google Gemini's free tier, whose terms allow use for product improvement and human review.",
  "Credentials (CSR-1, Darpan, 12A, 80G) are self-declared and not checked against government registries.",
  "FundBridge never processes payments, never auto-sends messages and never scrapes contact data.",
  "NGOs appear in directories only if they opt in. Donors are anonymous to NGOs unless they opt in.",
];

// Largest reported CSR spenders, scrolling under the hero. Names and totals come straight from the
// public filings loaded in the store; nothing is shown if the request fails.
function FunderMarquee() {
  const [data, setData] = useState(null);
  useEffect(() => { api.topSpenders().then(setData).catch(() => setData(null)); }, []);
  const list = data?.companies || [];
  if (!list.length) return null;
  const items = [...list, ...list]; // doubled so the -50% loop is seamless
  return (
    <div className="border-t border-white/10 bg-ink-900 py-5 text-white">
      <p className="mx-auto mb-3 max-w-7xl px-4 text-xs text-white/40 sm:px-6">
        Largest reported CSR spenders, total across all years in the MCA filings{data.data_source !== "REAL" ? ` (${data.data_source} data)` : ""}
      </p>
      <div className="group relative overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_8%,#000_92%,transparent)]">
        <ul className="flex w-max animate-marquee gap-10 group-hover:[animation-play-state:paused]">
          {items.map((c, i) => (
            <li key={i} aria-hidden={i >= list.length} className="flex shrink-0 items-baseline gap-2 whitespace-nowrap">
              <span className="text-lg font-medium tracking-snug">{c.company_name}</span>
              <span className="text-sm text-white/40">{inr(c.spend_inr)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function Auth({ status, demoMode, onDone, initialMode = "login", token = "" }) {
  const [mode, setMode] = useState(initialMode);
  const [f, setF] = useState({ email: "", password: "", name: "", role: "ngo", consent: false });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const go = async (fn) => {
    setErr(""); setMsg("");
    try { await fn(); } catch (e) { setErr(e.message); }
  };
  const submit = (e) => {
    e.preventDefault();
    if (mode === "login") go(async () => { await api.login(f.email, f.password); onDone(); });
    if (mode === "signup") go(async () => { const r = await api.signup(f); setMsg(r.message); onDone(); });
    if (mode === "forgot") go(async () => setMsg((await api.requestReset(f.email)).message));
    if (mode === "reset") go(async () => { await api.reset(token, f.password); setMsg("Password changed. Log in with the new password."); setMode("login"); });
  };

  const DEMO = [["NGO", "team@shiksha-setu.demo.fundbridge.local"], ["Company", "csr@thar-minerals.demo.fundbridge.local"],
                ["Donor", "ananya@donor.demo.fundbridge.local"]];
  const title = { login: "Welcome back", signup: "Create your account", forgot: "Reset your password", reset: "Choose a new password" }[mode];
  const linkBtn = "font-medium text-ink-900 underline-offset-4 transition hover:underline";
  const dq = status?.data_quality || {};
  const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-IN") : null);
  const stats = [
    fmt(dq.companies) && `${fmt(dq.companies)} companies`,
    fmt(dq.rows_loaded) && `${fmt(dq.rows_loaded)} CSR projects`,
    dq.fy_range?.length === 2 && `FY ${dq.fy_range[0]} to ${dq.fy_range[1]}`,
  ].filter(Boolean);

  return (
    <div className="flex-1">
      <section className="bg-ink-900 px-4 pb-16 pt-10 text-white sm:px-6 lg:pb-24">
        <div className="mx-auto grid max-w-7xl items-end gap-10 lg:grid-cols-[1.25fr_1fr]">
          <div className="animate-fade-up">
            <h1 className="text-[clamp(3.5rem,11vw,9.5rem)] font-semibold leading-[0.9] tracking-[-0.065em] text-white">FundBridge</h1>
            {stats.length > 0 && (
              <ul className="mt-8 flex flex-wrap gap-x-8 gap-y-2 text-lg font-medium tracking-snug sm:text-xl">
                {stats.map((x) => <li key={x}>{x}</li>)}
              </ul>
            )}
            <p className="mt-8 max-w-xl text-xl font-medium leading-snug tracking-snug sm:text-2xl">
              Find the companies already funding your cause.{" "}
              <span className="text-white/60">
                We rank CSR funders from public filings, draft the pitch and keep track of every follow-up, so small
                NGOs can spend their time on the work, not the search.
              </span>
            </p>
          </div>

          <form onSubmit={submit} className="animate-fade-up space-y-4 rounded-card bg-white p-6 text-ink-900 sm:p-8">
            {(mode === "login" || mode === "signup") && (
              <div className="grid grid-cols-2 rounded-full bg-ink-100 p-1 text-sm" role="tablist">
                {[["login", "Log in"], ["signup", "Create account"]].map(([k, label]) => (
                  <button key={k} type="button" role="tab" aria-selected={mode === k} onClick={() => { setMode(k); setErr(""); setMsg(""); }}
                    className={`rounded-full py-2 font-medium transition duration-300 ease-smooth ${mode === k ? "bg-ink-900 text-white" : "text-ink-500 hover:text-ink-900"}`}>
                    {label}
                  </button>
                ))}
              </div>
            )}
            <h2 className="text-3xl font-semibold tracking-tightest">{title}</h2>
            {mode === "signup" && (
              <>
                <div className="grid gap-2">
                  {ROLES.map(([k, label, hint]) => (
                    <label key={k} className={`flex cursor-pointer gap-3 rounded-field p-3.5 text-sm ring-1 ring-inset transition duration-300 ease-smooth ${f.role === k ? "bg-ink-900 text-white ring-ink-900" : "ring-ink-900/10 hover:ring-ink-900/40"}`}>
                      <input type="radio" name="role" className="mt-1" checked={f.role === k} onChange={() => setF({ ...f, role: k })} />
                      <span><b className="font-semibold">{label}</b><span className={`block text-xs ${f.role === k ? "text-white/60" : "text-ink-500"}`}>{hint}</span></span>
                    </label>
                  ))}
                </div>
                <div><label className="label">{f.role === "donor" ? "Display name" : "Organisation name"}</label>
                  <input required minLength={2} className="input" value={f.name} onChange={set("name")} /></div>
              </>
            )}
            {mode !== "reset" && (
              <div><label className="label">Email</label><input required type="email" className="input" value={f.email} onChange={set("email")} autoComplete="username" placeholder="you@organisation.org" /></div>
            )}
            {mode !== "forgot" && (
              <div><label className="label">{mode === "reset" ? "New password" : "Password"}</label>
                <input required type="password" minLength={mode === "login" ? 1 : 8} className="input" value={f.password} onChange={set("password")}
                  autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder={mode === "login" ? "" : "At least 8 characters"} /></div>
            )}
            {mode === "signup" && (
              <label className="flex items-start gap-2 text-xs text-ink-500">
                <input type="checkbox" className="mt-0.5" checked={f.consent} onChange={set("consent")} required />
                I have read the privacy and consent notice below and agree to it.
              </label>
            )}
            <Err msg={err} /><Ok msg={msg} />
            <button className="btn-primary group min-h-[52px] w-full text-[15px]">
              {{ login: "Log in", signup: "Create account", forgot: "Send reset link", reset: "Set password" }[mode]}
              <span aria-hidden className="transition-transform duration-300 ease-smooth group-hover:translate-x-1">→</span>
            </button>
            <div className="flex justify-between text-xs">
              {mode === "login" && <button type="button" className={linkBtn} onClick={() => setMode("forgot")}>Forgot password?</button>}
              {(mode === "forgot" || mode === "reset") && <button type="button" className={linkBtn} onClick={() => setMode("login")}>Back to log in</button>}
            </div>
            {demoMode && mode === "login" && (
              <div className="rounded-field bg-ink-100 p-4">
                <p className="text-xs font-medium text-ink-500">Try a demo account</p>
                <div className="mt-2.5 flex flex-wrap gap-2">
                  {DEMO.map(([label, email]) => (
                    <button key={email} type="button" className="btn-ghost min-h-[34px] px-4 py-1 text-xs"
                      onClick={() => setF({ ...f, email, password: "demo-pass-2026" })}>{label}</button>
                  ))}
                </div>
                <p className="mt-2.5 text-[11px] text-ink-500">Fills in the form; password <code>demo-pass-2026</code> unless DEMO_PASSWORD is set.</p>
              </div>
            )}
          </form>
        </div>
      </section>
      <FunderMarquee />

      <section className="px-4 py-14 sm:px-6">
        <div className="mx-auto grid max-w-7xl gap-8 lg:grid-cols-[1fr_2fr]">
          <h2 className="text-4xl font-semibold leading-none tracking-tightest sm:text-5xl">Privacy and consent</h2>
          <ol className="divide-y divide-ink-900/10 border-t border-ink-900/10">
            {PRIVACY_NOTICE.map((p, i) => (
              <li key={p} className="grid grid-cols-[3.5rem_1fr] gap-4 py-4 text-[15px] leading-relaxed">
                <span className="eyebrow text-ink-400">{String(i + 1).padStart(3, "0")}</span>
                <span className="text-ink-700">{p}</span>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  );
}

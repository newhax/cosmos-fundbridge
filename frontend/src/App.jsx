import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler } from "./api.js";
import { SourcesProvider } from "./components/Sources.jsx";
import { FeedbackButton, LangUnitControls, NotificationsBell, OfflineBanner } from "./components/shell.jsx";
import { Demo } from "./components/ui.jsx";
import { useI18n } from "./i18n/index.jsx";
import Auth from "./screens/Auth.jsx";
import CompanyDirectory from "./screens/company/Directory.jsx";
import CompanyInbox from "./screens/company/Inbox.jsx";
import CompanyPipeline from "./screens/company/Pipeline.jsx";
import CompanyProfile from "./screens/company/Profile.jsx";
import DonorDiscover from "./screens/donor/Discover.jsx";
import DonorLists from "./screens/donor/Lists.jsx";
import DonorProfile from "./screens/donor/Profile.jsx";
import FollowUps from "./screens/FollowUps.jsx";
import Funders from "./screens/Funders.jsx";
import NgoProfile from "./screens/NgoProfile.jsx";
import NgoSupporters from "./screens/NgoSupporters.jsx";
import Onboarding from "./screens/Onboarding.jsx";
import Outreach from "./screens/Outreach.jsx";

// Less-used screens load on demand (smaller first download on slow connections).
const Admin = lazy(() => import("./screens/Admin.jsx"));
const CompanyProjects = lazy(() => import("./screens/company/Projects.jsx"));
const Coverage = lazy(() => import("./screens/Coverage.jsx"));
const Projects = lazy(() => import("./screens/Projects.jsx"));

// [tab key, label key]
const TABS = {
  ngo: [["profile", "nav.profile"], ["onboarding", "nav.onboarding"], ["funders", "nav.funders"], ["outreach", "nav.outreach"],
        ["followups", "nav.followups"], ["projects", "nav.projects"], ["supporters", "nav.supporters"], ["coverage", "nav.coverage"]],
  company: [["profile", "nav.company_profile"], ["inbox", "nav.inbox"], ["projects", "nav.projects"], ["pipeline", "nav.pipeline"],
            ["directory", "nav.directory"], ["coverage", "nav.coverage"]],
  donor: [["discover", "nav.discover"], ["lists", "nav.lists"], ["profile", "nav.donor_profile"], ["coverage", "nav.coverage"]],
  admin: [["queues", "nav.admin_queues"], ["sectors", "nav.admin_sectors"], ["portals", "nav.admin_portals"],
          ["feedback", "nav.admin_feedback"], ["coverage", "nav.coverage"]],
};

function readParam(name) {
  try { return new URLSearchParams(window.location.search).get(name) || ""; } catch { return ""; }
}

export default function App() {
  const { t, lang, setLang } = useI18n();
  const [status, setStatus] = useState(null);
  const [me, setMe] = useState(undefined); // undefined = loading, null = logged out
  const [tab, setTab] = useState("");
  const [run, setRun] = useState(null);
  const [selected, setSelected] = useState(null);
  const [notice, setNotice] = useState("");
  const [publicView, setPublicView] = useState(readParam("view") === "coverage" ? "coverage" : "");
  const [unit, setUnitState] = useState(0); // bumps to re-render amounts after the unit changes

  const loadMe = useCallback(async () => {
    try {
      const m = await api.me();
      setMe(m);
      if (m.language && m.language !== lang) setLang(m.language);
      setTab((cur) => (cur && TABS[m.role].some(([k]) => k === cur) ? cur : TABS[m.role][0][0]));
      if (m.role === "ngo") api.latestRun().then(setRun).catch(() => setRun(null));
    } catch { setMe(null); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    api.status().then(setStatus).catch(() => setStatus({ error: true }));
    const verify = readParam("verify");
    if (verify) {
      api.verify(verify).then(() => setNotice("Email verified.")).catch((e) => setNotice(e.message))
        .finally(() => { window.history.replaceState({}, "", "/"); loadMe(); });
    } else loadMe();
  }, [loadMe]);

  // The ranking arrives first; model explanations are written in the background. Poll until they land.
  useEffect(() => {
    if (run?.explanations !== "pending") return undefined;
    const id = setInterval(() => {
      api.latestRun().then((r) => { if (r.run_id === run.run_id && r.explanations !== "pending") setRun(r); }).catch(() => {});
    }, 2000);
    return () => clearInterval(id);
  }, [run?.run_id, run?.explanations]);

  const logout = async () => { await api.logout(); setMe(null); setRun(null); setTab(""); };
  const resetToken = readParam("reset");

  const tabs = me ? TABS[me.role] : [];
  const current = tabs.find(([k]) => k === tab);
  const subtitle = tab === "coverage" ? t("sub.coverage") : me?.role === "admin" ? t("sub.admin") : t(`sub.${me?.role}:${tab}`);
  return (
    <SourcesProvider>
      <a href="#main" className="sr-only z-50 rounded bg-white px-3 py-2 text-ink-900 focus:not-sr-only focus:fixed focus:left-3 focus:top-3">{t("common.skip")}</a>
      <OfflineBanner />
      <div className="flex min-h-screen flex-col">
        <div className="bg-ink-900 text-white">
          <Header status={status} me={me} onLogout={logout} tabs={tabs} tab={tab} setTab={setTab}
            onUnit={() => setUnitState((n) => n + 1)} onPublic={setPublicView} publicView={publicView} />
          {me && current && (
            <div className="mx-auto w-full max-w-7xl px-4 pb-10 pt-8 sm:px-6">
              <p className="eyebrow text-white/60">
                <span className="text-white">{String(tabs.findIndex(([k]) => k === tab) + 1).padStart(3, "0")}</span>
                <span className="mx-2">/</span>{t(`role.${me.role}`)}
              </p>
              <h1 key={tab} className="mt-4 animate-fade-up text-4xl font-semibold leading-[1.1] tracking-tightest text-white sm:text-6xl">{t(current[1])}</h1>
              {subtitle && !subtitle.startsWith("sub.") && <p className="mt-4 max-w-2xl text-lg leading-snug tracking-snug text-white/60">{subtitle}</p>}
            </div>
          )}
          {me === null && publicView === "coverage" && (
            <div className="mx-auto w-full max-w-7xl px-4 pb-10 pt-8 sm:px-6">
              <h1 className="text-4xl font-semibold tracking-tightest text-white sm:text-6xl">{t("nav.coverage")}</h1>
              <p className="mt-4 max-w-2xl text-lg text-white/60">{t("sub.coverage")}</p>
            </div>
          )}
        </div>

        {me === null && !publicView && <Auth status={status} demoMode={status?.demo_mode} onDone={loadMe} initialMode={resetToken ? "reset" : "login"} token={resetToken} />}
        {me === null && publicView === "coverage" && (
          <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
            <Suspense fallback={<p className="text-sm text-ink-500">{t("common.loading")}</p>}><Coverage status={status} /></Suspense></main>
        )}

        <div className={`page-bg ${me ? "flex-1" : ""}`}>
          <div className={`mx-auto w-full max-w-7xl space-y-2 px-4 pt-4 sm:px-6 ${me ? "" : "hidden"}`}>
            {status?.data_source === "SAMPLE" && (
              <Notice tone="sand"><b>Sample data, not real filings.</b> Companies and amounts are synthetic and fictional.</Notice>
            )}
            <Notice tone="ink">
              Demo mode: do not enter confidential data. Inputs go to Gemini's free tier, whose terms allow use for product improvement and human review.
            </Notice>
            {status?.warnings?.map((w) => <Notice key={w} tone="coral">⚠ {w}</Notice>)}
            {run?.capacity_reached && me?.role === "ngo" && <Notice tone="sand">{t("common.capacity")}</Notice>}
            {notice && <Notice tone="sage">{notice}</Notice>}
            {me && <AccountBanners me={me} />}
          </div>

          {me && (
            <main id="main" key={`${tab}-${unit}`} className="mx-auto w-full max-w-7xl animate-fade-up px-4 py-6 sm:px-6">
              <Suspense fallback={<p className="text-sm text-ink-500">{t("common.loading")}</p>}>
              {tab === "coverage" && <Coverage status={status} />}
              {me.role === "ngo" && (
                <>
                  {tab === "profile" && <NgoProfile status={status} />}
                  {tab === "onboarding" && <Onboarding status={status} me={me} current={run?.ngo} onRun={(r) => { setRun(r); setTab("funders"); }} />}
                  {tab === "funders" && <Funders run={run} onOutreach={(id) => { setSelected(id); setTab("outreach"); }} />}
                  {tab === "outreach" && <Outreach run={run} me={me} selected={selected} setSelected={setSelected} />}
                  {tab === "followups" && <FollowUps run={run} />}
                  {tab === "projects" && <Projects status={status} />}
                  {tab === "supporters" && <NgoSupporters />}
                </>
              )}
              {me.role === "company" && (
                <>
                  {tab === "profile" && <CompanyProfile status={status} me={me} onSaved={loadMe} />}
                  {tab === "inbox" && <CompanyInbox me={me} />}
                  {tab === "projects" && <CompanyProjects status={status} />}
                  {tab === "pipeline" && <CompanyPipeline me={me} />}
                  {tab === "directory" && <CompanyDirectory status={status} onEditProfile={() => setTab("profile")} />}
                </>
              )}
              {me.role === "donor" && (
                <>
                  {tab === "discover" && <DonorDiscover status={status} />}
                  {tab === "lists" && <DonorLists />}
                  {tab === "profile" && <DonorProfile status={status} />}
                </>
              )}
              {me.role === "admin" && tab !== "coverage" && <Admin tab={tab} status={status} />}
              </Suspense>
            </main>
          )}
        </div>

        <footer className="bg-ink-900 px-4 py-10 text-sm text-white/60 sm:px-6">
          <div className="mx-auto flex max-w-7xl flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="space-y-3">
              <div className="text-3xl font-semibold tracking-tightest text-white sm:text-4xl">FundBridge</div>
              {!me && (
                <button className="text-sm text-white underline-offset-4 hover:underline" onClick={() => setPublicView(publicView ? "" : "coverage")}>
                  {publicView ? "← Log in" : `${t("nav.coverage")} →`}
                </button>
              )}
            </div>
            <div className="max-w-xl space-y-1 sm:text-right">
              <p>{t("footer.note")}</p>
              <p className="text-white/60">{t("footer.privacy")}</p>
            </div>
          </div>
        </footer>
      </div>
    </SourcesProvider>
  );
}

const TONES = {
  sand: "bg-sand-50 text-sand-900", ink: "bg-white text-ink-500", coral: "bg-coral-50 text-coral-800",
  sage: "bg-sage-50 text-sage-900", mist: "bg-mist-50 text-mist-900",
};
const DOT = { sand: "bg-sand-500", ink: "bg-ink-300", coral: "bg-coral-600", sage: "bg-sage-500", mist: "bg-mist-500" };

function Notice({ tone = "ink", children }) {
  return (
    <div role="note" className={`flex items-start gap-2.5 rounded-field px-4 py-2.5 text-xs leading-relaxed ${TONES[tone]}`}>
      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden />
      <span>{children}</span>
    </div>
  );
}

function AccountBanners({ me }) {
  const [sent, setSent] = useState(false);
  return (
    <>
      {!me.email_verified && (
        <Notice tone="mist">
          Verify your email to send pitches or replies. DEMO MODE: the link is in the server console.{" "}
          <button className="font-semibold underline" onClick={() => api.resendVerification().then(() => setSent(true))}>{sent ? "Sent again" : "Resend link"}</button>
        </Notice>
      )}
    </>
  );
}

const ROLE_NAME = { ngo: "NGO", company: "Company", donor: "Donor", admin: "Admin" };

function Header({ me, status, onLogout, tabs, tab, setTab, onUnit }) {
  const { t } = useI18n();
  const who = me?.profile?.name || me?.profile?.display_name || me?.email;
  return (
    <header className="sticky top-0 z-30 bg-ink-900/95 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
        {/* Logo: soft white glow on hover */}
        <div className="group flex cursor-default items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-[10px] bg-white text-lg font-bold tracking-tighter text-ink-900 transition duration-300 ease-smooth group-hover:scale-105 group-hover:shadow-[0_0_14px_3px_rgba(255,255,255,.55),0_0_36px_8px_rgba(255,255,255,.25)]">F</span>
          <span className="text-lg font-semibold tracking-tighter text-white transition duration-300 ease-smooth group-hover:[text-shadow:0_0_10px_rgba(255,255,255,.8),0_0_24px_rgba(255,255,255,.45)]">FundBridge</span>
        </div>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {status?.error && <span className="badge bg-coral-600 text-white">API offline</span>}
          <LangUnitControls me={me} languages={status?.languages} onUnit={onUnit} />
          <FeedbackButton page={tab || "login"} me={me} />
          {me && (
            <>
              <NotificationsBell onOpenTab={(k) => tabs.some(([x]) => x === k) && setTab(k)} />
              <div className="hidden max-w-[180px] text-right text-xs leading-tight md:block">
                <div className="truncate font-medium text-white" title={who}>{who}<Demo on={me.is_demo} /></div>
                <div className="text-white/60">{ROLE_NAME[me.role] || me.role}</div>
              </div>
              <button className="btn-on-dark min-h-[40px]" onClick={onLogout}>{t("common.logout")}</button>
            </>
          )}
        </div>
      </div>

      {me && (
        <nav aria-label="Sections" className="mx-auto w-full max-w-7xl px-4 pb-3 sm:px-6">
          <div className="flex max-w-full gap-1 overflow-x-auto md:inline-flex md:rounded-full md:bg-white md:p-1 md:shadow-btn">
            {tabs.map(([k, labelKey]) => (
              <button key={k} onClick={() => setTab(k)} aria-current={tab === k ? "page" : undefined}
                className={`shrink-0 whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors duration-300 ease-smooth md:px-4 md:py-2 ${tab === k
                  ? "bg-white text-ink-900 md:bg-ink-900 md:text-white"
                  : "text-white/60 ring-1 ring-inset ring-white/15 hover:text-white md:text-ink-900 md:ring-0 md:hover:bg-ink-100"}`}>
                {t(labelKey)}
              </button>
            ))}
          </div>
        </nav>
      )}
    </header>
  );
}

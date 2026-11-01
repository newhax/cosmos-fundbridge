// All calls are same-origin, so the httpOnly session cookie is sent automatically.
// No ids for "me" are ever sent: the server derives them from the session.
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function req(method, path, body, raw) {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: raw ? { "Content-Type": "text/csv" } : body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized();
  if (!res.ok) {
    const d = data.detail;
    // FastAPI validation errors are a list of {loc, msg}; show them as readable text.
    const msg = typeof d === "string" ? d
      : Array.isArray(d) ? d.map((x) => `${x.loc?.at(-1) ?? "input"}: ${x.msg}`).join("; ")
      : d?.message || (d ? JSON.stringify(d) : `HTTP ${res.status}`);
    const err = new Error(msg);
    err.status = res.status;
    err.detail = d;
    throw err;
  }
  return data;
}

const enc = encodeURIComponent;
const get = (p) => req("GET", p);
const post = (p, b = {}) => req("POST", p, b);
const put = (p, b) => req("PUT", p, b);

export const api = {
  // public / any role
  status: () => get("/status"),
  topSpenders: () => get("/top-spenders"),
  rows: (refs) => post("/rows", { refs }),
  // auth
  me: () => get("/auth/me"),
  signup: (b) => post("/auth/signup", b),
  login: (email, password) => post("/auth/login", { email, password }),
  logout: () => post("/auth/logout"),
  verify: (token) => post("/auth/verify", { token }),
  resendVerification: () => post("/auth/resend-verification"),
  requestReset: (email) => post("/auth/request-reset", { email }),
  reset: (token, new_password) => post("/auth/reset", { token, new_password }),
  // NGO: pipeline + email outreach + follow-ups
  run: (profile) => post("/run", profile),
  latestRun: () => get("/runs/latest"),
  replayRun: (runId) => post(`/runs/${runId}/replay`),
  outreach: (companyId, regenerate = false) => post("/outreach", { company_id: companyId, regenerate }),
  listOutreach: () => get("/outreach"),
  saveDrafts: (companyId, drafts) => put(`/outreach/${enc(companyId)}`, { drafts }),
  markSent: (companyId, draft) => post(`/outreach/${enc(companyId)}/sent`, { ...draft, approved: true }),
  followups: () => get("/followups"),
  addEvent: (companyId, ev) => post(`/followups/${enc(companyId)}/events`, ev),
  draftNext: (key) => post(`/followups/${enc(key)}/draft`),
  // NGO: profile, in-app pitches, donors
  ngoProfile: () => get("/ngo/profile"),
  saveNgoProfile: (fields) => put("/ngo/profile", fields),
  platformMatch: (cin) => get(`/ngo/platform-match/${enc(cin)}`),
  sendPitch: (b) => post("/ngo/pitches", { ...b, approved: true }),
  myPitches: () => get("/ngo/pitches"),
  myPitch: (id) => get(`/ngo/pitches/${enc(id)}`),
  draftUpdate: (facts) => post("/ngo/updates/draft", { facts }),
  postUpdate: (title, body) => post("/ngo/updates", { title, body, approved: true }),
  myUpdates: () => get("/ngo/updates"),
  supporters: () => get("/ngo/supporters"),
  confirmGift: (id) => post(`/ngo/gifts/${enc(id)}/confirm`),
  // company
  companyProfile: () => get("/company/profile"),
  saveCompanyProfile: (fields) => put("/company/profile", fields),
  footprint: () => get("/company/footprint"),
  directory: (f) => get(`/company/directory?sector=${enc(f.sector || "")}&state=${enc(f.state || "")}&district=${enc(f.district || "")}`),
  inbox: () => get("/company/inbox"),
  companyPitch: (id) => get(`/company/pitches/${enc(id)}`),
  screen: (id) => post(`/company/pitches/${enc(id)}/screen`),
  draftReply: (id, action) => post(`/company/pitches/${enc(id)}/draft-reply`, { action }),
  respond: (id, b) => post(`/company/pitches/${enc(id)}/respond`, { ...b, approved: true }),
  reportPitch: (id, reason) => post(`/company/pitches/${enc(id)}/report`, { reason }),
  pipeline: () => get("/company/pipeline"),
  // donor
  donorProfile: () => get("/donor/profile"),
  saveDonorProfile: (f) => put("/donor/profile", f),
  discover: (f = {}) => get(`/donor/discover?cause=${enc(f.cause || "")}&state=${enc(f.state || "")}`),
  donorMemory: () => get("/donor/memory"),
  donorLists: () => get("/donor/lists"),
  shortlist: (ngoId, on) => post(`/donor/shortlist/${enc(ngoId)}`, { on }),
  follow: (ngoId, on, share_name = null) => post(`/donor/follow/${enc(ngoId)}`, { on, share_name }),
  feed: () => get("/donor/feed"),
  give: (ngoId) => get(`/donor/give/${enc(ngoId)}`),
  reportGift: (b) => post("/donor/gifts", b),
  myGifts: () => get("/donor/gifts"),
  reportNgo: (ngoId, reason) => post(`/donor/report/${enc(ngoId)}`, { reason }),
  // verification
  myVerification: () => get("/verification/me"),
  setLanguage: (language) => put("/auth/me/language", { language }),
  // geography + coverage (public)
  geoStates: () => get("/geo/states"),
  geoDistricts: (state) => get(`/geo/states/${enc(state)}/districts`),
  coverage: (sector = "") => get(`/coverage?sector=${enc(sector)}`),
  coverageState: (state, sector = "") => get(`/coverage/${enc(state)}?sector=${enc(sector)}`),
  statePortals: () => get("/state-portals"),
  // projects, calls, requests, project management
  myProjects: () => get("/ngo/projects"),
  saveProject: (b, id) => (id ? put(`/ngo/projects/${enc(id)}`, b) : post("/ngo/projects", b)),
  platformCompanies: () => get("/ngo/companies"),
  requestFunds: (projectId, b) => post(`/ngo/projects/${enc(projectId)}/request-funds`, b),
  openCalls: (page = 1) => get(`/ngo/calls?page=${page}`),
  submitProposal: (callId, b) => post(`/ngo/calls/${enc(callId)}/proposals`, b),
  ngoRequests: () => get("/ngo/requests"),
  withdrawRequest: (id) => post(`/ngo/requests/${enc(id)}/withdraw`),
  browseProjects: (f = {}, page = 1) => get(`/company/projects?sector=${enc(f.sector || "")}&state=${enc(f.state || "")}&page=${page}`),
  myCalls: () => get("/company/calls"),
  createCall: (b) => post("/company/calls", b),
  closeCall: (id) => post(`/company/calls/${enc(id)}/close`),
  companyRequests: () => get("/company/requests"),
  decideRequest: (id, accept, note) => post(`/company/requests/${enc(id)}/decide`, { accept, note }),
  engagements: () => get("/engagements"),
  addUpdate: (id, b) => post(`/engagements/${enc(id)}/updates`, b),
  milestoneDone: (id, updateId, done) => post(`/engagements/${enc(id)}/milestones/${enc(updateId)}`, { done }),
  completeEngagement: (id) => post(`/engagements/${enc(id)}/complete`),
  notifications: () => get("/notifications"),
  notificationsRead: () => post("/notifications/read"),
  feedback: (b) => post("/feedback", b),
  translateDraft: (companyId, lang, tone) => post(`/outreach/${enc(companyId)}/translate`, { lang, tone }),
  // scoped admin
  adminScope: () => get("/admin/scope"),
  adminUnmapped: (status = "open", state = "") => get(`/admin/geo/unmapped?status=${enc(status)}&state=${enc(state)}`),
  adminResolve: (id, target_code) => post(`/admin/geo/unmapped/${id}`, { target_code }),
  adminSectors: () => get("/admin/sectors"),
  adminMapLabel: (label, sector) => post("/admin/sectors/labels", { label, sector }),
  adminSectorItems: (sector, items, note) => put(`/admin/sectors/${enc(sector)}`, { items, note }),
  adminSetPortal: (state, url, note) => put(`/admin/state-portals/${enc(state)}`, { url, note }),
  adminFeedback: (f = {}) => get(`/admin/feedback?status=${enc(f.status || "")}&state=${enc(f.state || "")}&language=${enc(f.language || "")}`),
  adminFeedbackStatus: (id, status) => post(`/admin/feedback/${enc(id)}`, { status }),
};

// Amount unit chosen in the header: auto (lakh/crore as appropriate) | rupees | lakh | crore. Always shows the unit.
let UNIT = (() => { try { return localStorage.getItem("fb_unit") || "auto"; } catch { return "auto"; } })();
export const getUnit = () => UNIT;
export function setUnit(u) {
  UNIT = u;
  try { localStorage.setItem("fb_unit", u); } catch { /* private mode */ }
}
const grouped = (n, digits = 0) => n.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });

// DD/MM/YYYY in IST (India Standard Time).
export function fmtDate(iso) {
  if (!iso) return "unknown";
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T00:00:00+05:30`) : new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("en-GB", { timeZone: "Asia/Kolkata", day: "2-digit", month: "2-digit", year: "numeric" });
}

// Indian financial year, April to March.
export function fiscalYear(iso) {
  const d = new Date(iso);
  const start = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  return `${start}-${String(start + 1).slice(-2)}`;
}

export function inr(v) {
  if (v === null || v === undefined || v === "") return "unknown";
  if (UNIT === "rupees") return `₹${grouped(Math.round(v))}`;
  if (UNIT === "lakh") return `₹${grouped(v / 1e5, 2)} lakh`;
  if (UNIT === "crore") return `₹${grouped(v / 1e7, 2)} crore`;
  if (v >= 1e7) return `₹${(v / 1e7).toFixed(2)} crore`;
  if (v >= 1e5) return `₹${(v / 1e5).toFixed(1)} lakh`;
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

export const STATES = ["Andhra Pradesh", "Assam", "Bihar", "Chhattisgarh", "Delhi", "Goa", "Gujarat", "Haryana",
  "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Odisha", "Punjab",
  "Rajasthan", "Tamil Nadu", "Telangana", "Uttar Pradesh", "Uttarakhand", "West Bengal"];

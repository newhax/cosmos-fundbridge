import { useEffect, useState } from "react";
import { api } from "../api.js";
import { AreaPicker, toPayload } from "../components/geo.jsx";
import { CauseChips, CredBadge, Err, Field, Ok, Toggle } from "../components/ui.jsx";
import NgoListingCard from "../components/NgoListingCard.jsx";
import VerificationBadge from "../components/VerificationBadge.jsx";

const LISTABLE = [["state", "State"], ["district", "District"], ["causes", "Causes"], ["summary", "Summary"], ["credentials", "Credentials (self-declared)"]];

export default function NgoProfile({ status }) {
  const [data, setData] = useState(null);
  const [f, setF] = useState(null);
  const [ver, setVer] = useState(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => {
    api.ngoProfile().then((d) => { setData(d); setF(d.profile); }).catch((e) => setErr(e.message));
    api.myVerification().then(setVer).catch(() => {});
  };
  useEffect(load, []);
  if (!f) return <p className="text-slate-500">Loading profile…</p>;

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    setErr(""); setMsg("");
    try {
      const fields = {};
      fields.hq_area = f.hq_area ? toPayload([f.hq_area])[0] : [];
      fields.areas = toPayload(f.areas);
      ["name", "causes", "summary", "crn", "darpan_id", "has_12a", "has_80g", "pan", "is_section8", "section8_cin",
        "receives_foreign_funds", "listed_publicly", "listed_fields", "accepts_individual_donations", "donation_url",
        "impact_updates_enabled", "founded_year", "suggested_gift_inr"].forEach((k) => { fields[k] = f[k]; });
      const d = await api.saveNgoProfile(fields);
      setData(d); setF(d.profile); setMsg("Saved.");
      api.myVerification().then(setVer).catch(() => {});
    } catch (e) { setErr(e.message); }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card space-y-3 lg:col-span-2">
        <h2 className="text-lg font-semibold">Organisation profile</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="NGO name"><input className="input" value={f.name} onChange={set("name")} /></Field>
          <Field label="Founded (year)" hint="Shown to donors as years active (self-declared)"><input type="number" className="input" value={f.founded_year || ""} onChange={set("founded_year")} /></Field>
        </div>
        <Field label="Headquarters" hint="One state/UT, optionally a district (from the official LGD list)">
          <AreaPicker single allowAllIndia={false} idPrefix="hq" value={f.hq_area ? [f.hq_area] : []}
            onChange={(v) => setF({ ...f, hq_area: v[0] || null })} /></Field>
        <Field label="Where you work" hint="Districts, whole states, or all India. Companies and calls are matched on these.">
          <AreaPicker idPrefix="ops" value={f.areas || []} onChange={(v) => setF({ ...f, areas: v })} /></Field>
        <Field label="Causes"><CauseChips options={status?.sectors || []} value={f.causes} onChange={(v) => setF({ ...f, causes: v })} placeholder="e.g. animal welfare, mental health" /></Field>
        <Field label="Summary"><textarea className="input h-20" value={f.summary || ""} onChange={set("summary")} /></Field>

        <h3 className="border-t pt-3 font-semibold">Credentials (self-declared)</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="CSR-1 registration number"><input className="input" value={f.crn || ""} onChange={set("crn")} /></Field>
          <Field label="NGO Darpan ID" hint="Format: XX/YYYY/NNNNNNN"><input className="input" value={f.darpan_id || ""} onChange={set("darpan_id")} /></Field>
          <Field label="PAN" hint="Format and duplicate checks only; shown to companies as self-declared"><input className="input" value={f.pan || ""} onChange={set("pan")} /></Field>
          <div className="space-y-1 pt-4">
            <Toggle checked={f.has_12a} onChange={(v) => setF({ ...f, has_12a: v })} label="Has 12A registration" />
            <Toggle checked={f.has_80g} onChange={(v) => setF({ ...f, has_80g: v })} label="Has 80G registration" />
            <Toggle checked={f.is_section8} onChange={(v) => setF({ ...f, is_section8: v })} label="Section 8 company" />
            <Toggle checked={f.receives_foreign_funds} onChange={(v) => setF({ ...f, receives_foreign_funds: v })} label="Receives foreign contributions (FCRA)" hint="Individual donations on FundBridge are India / INR only." />
          </div>
          {f.is_section8 && <Field label="Section 8 CIN"><input className="input" value={f.section8_cin || ""} onChange={set("section8_cin")} /></Field>}
        </div>

        <h3 className="border-t pt-3 font-semibold">Visibility (opt-in)</h3>
        <Toggle checked={f.listed_publicly} onChange={(v) => setF({ ...f, listed_publicly: v })} label="List my NGO in the company and donor directories"
          hint="Off by default. Only the fields you tick below are shown, plus your name." />
        <div className="flex flex-wrap gap-3 pl-6">
          {LISTABLE.map(([k, label]) => (
            <Toggle key={k} checked={(f.listed_fields || []).includes(k)} label={label}
              onChange={(on) => setF({ ...f, listed_fields: on ? [...(f.listed_fields || []), k] : (f.listed_fields || []).filter((x) => x !== k) })} />
          ))}
        </div>

        <h3 className="border-t pt-3 font-semibold">Individual donations</h3>
        <Toggle checked={f.accepts_individual_donations} onChange={(v) => setF({ ...f, accepts_individual_donations: v })}
          label="Accept individual donations" hint="Donors are sent to your own page. FundBridge never handles payments, receipts or 80G." />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Donation page URL (https only)"><input className="input" value={f.donation_url || ""} onChange={set("donation_url")} placeholder="https://your-ngo.org/donate" /></Field>
          <Field label="Suggested gift (INR)" hint="Used by the donor Matcher's giving-range fit"><input type="number" className="input" value={f.suggested_gift_inr || ""} onChange={set("suggested_gift_inr")} /></Field>
        </div>
        <Toggle checked={f.impact_updates_enabled} onChange={(v) => setF({ ...f, impact_updates_enabled: v })} label="Post impact updates to followers" />

        <Err msg={err} /><Ok msg={msg} />
        <button className="btn-primary" onClick={save}>Save profile</button>
      </div>

      <div className="space-y-4">
        <div className="card text-sm">
          <h3 className="mb-2 font-semibold">Readiness checklist</h3>
          {data.readiness.map((i) => (
            <div key={i.key} className="flex items-center justify-between border-b py-1 last:border-0">
              <span>{i.done ? "✅" : "⬜"} {i.label}</span>
              {i.done ? <CredBadge source={i.source} /> : <span className="text-xs text-slate-400">missing</span>}
            </div>
          ))}
          <p className="mt-2 text-[11px] text-slate-400">Companies see missing documents when screening your pitch.</p>
        </div>
        <div className="card text-sm">
          <h3 className="mb-2 font-semibold">Verification status</h3>
          {ver ? <VerificationBadge v={ver} detail /> : <p className="text-xs text-slate-500">Loading…</p>}
          {ver && (
            <ul className="mt-2 space-y-0.5 text-[11px] text-slate-600">
              {ver.auto_checks.map((a) => <li key={a.check}>{a.ok ? "✓" : "✗"} {a.detail}</li>)}
            </ul>
          )}
          <p className="mt-2 text-[11px] text-slate-400">Automatic checks only (ID formats and duplicate IDs). Nobody checks official records by hand, so FundBridge never says "verified".</p>
        </div>
        <div className="card text-sm">
          <DirectoryPreview data={data} unsaved={JSON.stringify(f) !== JSON.stringify(data.profile)} />
        </div>
      </div>
    </div>
  );
}

// What companies (and donors, if you accept donations) see, from your SAVED profile.

function DirectoryPreview({ data, unsaved }) {
  const listed = data.profile.listed_publicly;
  const shown = data.profile.listed_fields || [];
  const hidden = LISTABLE.filter(([k]) => !shown.includes(k)).map(([, label]) => label);
  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="font-semibold">Directory preview</h3>
        <span className={`badge ${listed ? "bg-sage-100 text-sage-900" : "bg-ink-100 text-ink-600"}`}>{listed ? "Listed" : "Not listed"}</span>
      </div>
      {listed ? (
        <>
          <p className="mb-2 text-xs text-ink-500">This is how companies see you in the NGO directory.</p>
          <NgoListingCard n={data.public_listing_preview} className="rounded-box border border-ink-200 p-4" />
          <p className="mt-2 text-[11px] text-ink-500">
            {hidden.length ? <>Not shown: {hidden.join(", ")}. </> : "All fields are shown. "}
            Change this under Visibility.
          </p>
        </>
      ) : (
        <p className="text-xs text-ink-500">
          Companies and donors cannot find you in directories. Tick "List my NGO" under Visibility and save to appear.
        </p>
      )}
      {unsaved && <p className="mt-2 rounded-box bg-sand-50 px-3 py-2 text-[11px] text-sand-900">You have unsaved changes. Save the profile to update this preview.</p>}
    </>
  );
}

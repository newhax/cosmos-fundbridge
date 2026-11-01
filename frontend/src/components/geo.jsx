// Geography pickers backed by the loaded LGD file (/api/geo/*). Areas are {level, state, district} with LGD codes.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useI18n } from "../i18n/index.jsx";

let STATES_CACHE = null;
const DISTRICTS_CACHE = {};

export function useStates() {
  const [states, setStates] = useState(STATES_CACHE?.states || []);
  useEffect(() => {
    if (STATES_CACHE) return;
    api.geoStates().then((r) => { STATES_CACHE = r; setStates(r.states); }).catch(() => setStates([]));
  }, []);
  return states;
}

export function useDistricts(state) {
  const [list, setList] = useState(DISTRICTS_CACHE[state] || []);
  useEffect(() => {
    if (!state) { setList([]); return; }
    if (DISTRICTS_CACHE[state]) { setList(DISTRICTS_CACHE[state]); return; }
    api.geoDistricts(state).then((r) => { DISTRICTS_CACHE[state] = r; setList(r); }).catch(() => setList([]));
  }, [state]);
  return list;
}

export function StateSelect({ value, onChange, id, allowAll = false, required = false, className = "input" }) {
  const { t } = useI18n();
  const states = useStates();
  return (
    <select id={id} aria-label={id ? undefined : t("area.state")} className={className} value={value || ""} required={required} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowAll ? "All states" : t("area.pick_state")}</option>
      {states.map((s) => <option key={s.lgd_code} value={s.lgd_code}>{s.name}{s.kind === "ut" ? " (UT)" : ""}</option>)}
    </select>
  );
}

export function DistrictSelect({ state, value, onChange, id, wholeLabel, className = "input" }) {
  const { t } = useI18n();
  const list = useDistricts(state);
  return (
    <select id={id} aria-label={id ? undefined : t("area.district")} className={className} value={value || ""} disabled={!state} onChange={(e) => onChange(e.target.value)}>
      <option value="">{wholeLabel || t("area.whole")}</option>
      {list.map((d) => <option key={d.lgd_code} value={d.lgd_code}>{d.name}</option>)}
    </select>
  );
}

export function areaLabel(a, t) {
  if (!a) return t ? t("common.unknown") : "unknown";
  if (a.label) return a.label;
  return a.level === "all_india" ? "All India" : `${a.district_name || a.district || ""} ${a.state_name || a.state}`.trim();
}

const plain = (a) => ({ level: a.level, state: a.state || "", district: a.district || "" });

// Many areas: districts, whole states, or All India. `value` holds areas as returned by the API.
export function AreaPicker({ value, onChange, single = false, allowAllIndia = true, idPrefix = "area" }) {
  const { t } = useI18n();
  const [st, setSt] = useState("");
  const [di, setDi] = useState("");
  const areas = value || [];
  const add = () => {
    if (!st) return;
    const a = di ? { level: "district", state: st, district: di } : { level: "state", state: st, district: "" };
    const next = single ? [a] : [...areas.map(plain).filter((x) => !(x.level === a.level && x.state === a.state && x.district === a.district)), a];
    onChange(next);
    setDi("");
  };
  const remove = (i) => onChange(areas.filter((_, j) => j !== i).map(plain));
  const allIndia = areas.some((a) => a.level === "all_india");
  return (
    <div className="space-y-2">
      {areas.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Selected areas">
          {areas.map((a, i) => (
            <li key={`${a.level}-${a.state}-${a.district}`} className="badge border border-ink-900 bg-ink-900 py-1 pl-3 pr-1.5 text-xs text-white">
              {areaLabel(a, t)}{a.flag && <span title={a.flag}> ⚠</span>}
              <button type="button" aria-label={`Remove ${areaLabel(a, t)}`} onClick={() => remove(i)}
                className="grid h-4 w-4 place-items-center rounded-full text-white/70 hover:bg-white/20 hover:text-white">×</button>
            </li>
          ))}
        </ul>
      )}
      {!allIndia && (
        <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <div><label className="sr-only" htmlFor={`${idPrefix}-state`}>{t("area.state")}</label>
            <StateSelect id={`${idPrefix}-state`} value={st} onChange={(v) => { setSt(v); setDi(""); }} /></div>
          <div><label className="sr-only" htmlFor={`${idPrefix}-district`}>{t("area.district")}</label>
            <DistrictSelect id={`${idPrefix}-district`} state={st} value={di} onChange={setDi} /></div>
          <button type="button" className="btn-ghost" onClick={add} disabled={!st}>{single ? t("common.save") : t("area.add")}</button>
        </div>
      )}
      {allowAllIndia && !single && (
        <label className="flex items-center gap-2 text-xs text-ink-600">
          <input type="checkbox" checked={allIndia}
            onChange={(e) => onChange(e.target.checked ? [{ level: "all_india", state: "", district: "" }] : [])} />
          {t("area.all_india")} (pan-India)
        </label>
      )}
    </div>
  );
}

export const toPayload = (areas) => (areas || []).map(plain);

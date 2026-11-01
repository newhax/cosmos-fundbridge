import { cloneElement, isValidElement, useId, useState } from "react";

// Small shared UI helpers used across role screens.
export const Demo = ({ on }) => (on ? <span className="badge ml-1 bg-sand-300 text-sand-950">DEMO</span> : null);

export function CredBadge({ source }) {
  // Never "verified": either the NGO said so, or it is unknown.
  if (!source || source === "unknown") return <span className="badge bg-slate-200 text-slate-600">unknown</span>;
  return <span className="badge bg-amber-100 text-amber-800">self-declared</span>;
}

// A labelled form field. A single <input>/<select>/<textarea> child gets an id tied to the label (and the hint
// as its description); composite children (chips, area pickers) get a group label instead.
export function Field({ label, children, hint }) {
  const id = useId();
  const single = isValidElement(children) && ["input", "select", "textarea"].includes(children.type);
  const child = single ? cloneElement(children, { id: children.props.id || id, "aria-describedby": hint ? `${id}-hint` : undefined }) : children;
  return (
    <div role={single ? undefined : "group"} aria-labelledby={single ? undefined : `${id}-label`}>
      {single ? <label className="label" htmlFor={child.props.id}>{label}</label> : <span id={`${id}-label`} className="label">{label}</span>}
      {child}
      {hint && <p id={`${id}-hint`} className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint }) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input type="checkbox" className="mt-1" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}{hint && <span className="block text-[11px] text-slate-400">{hint}</span>}</span>
    </label>
  );
}

export function Chips({ options, value, onChange }) {
  const v = value || [];
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((s) => (
        <button type="button" key={s} onClick={() => onChange(v.includes(s) ? v.filter((x) => x !== s) : [...v, s])}
          className={`badge cursor-pointer border px-3 py-1 text-xs transition duration-200 ${v.includes(s) ? "border-brand-600 bg-brand-600 text-white shadow-soft" : "border-ink-200 bg-white text-ink-600 hover:-translate-y-0.5 hover:border-ink-400"}`}
          aria-pressed={v.includes(s)}>
          {s}
        </button>
      ))}
    </div>
  );
}

// Standard sectors as chips, plus "Other" to type causes of your own (same rules as the server's
// sectors.clean_causes: up to 5, 2-40 characters). Typed causes are matched by exact wording only.
const MAX_CUSTOM = 5;
const CUSTOM_RE = /^[a-z0-9][a-z0-9 &,'()/.-]{1,39}$/;

export function CauseChips({ options, value, onChange, placeholder = "e.g. animal welfare", hint }) {
  const v = value || [];
  const custom = v.filter((c) => !options.includes(c));
  const [open, setOpen] = useState(custom.length > 0);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const add = () => {
    const c = text.replace(/\s+/g, " ").trim().toLowerCase();
    if (!c) return;
    if (c === "other") return setErr("Type the cause itself, e.g. animal welfare.");
    if (!CUSTOM_RE.test(c)) return setErr("Use 2-40 letters, numbers, spaces or & , ' ( ) / . -");
    if (v.includes(c)) return setErr(options.includes(c) ? "That one is in the list above: select it there." : "Already added.");
    if (options.includes(c)) { onChange([...v, c]); setText(""); setErr(""); return; }
    if (custom.length >= MAX_CUSTOM) return setErr(`You can add up to ${MAX_CUSTOM} causes of your own.`);
    onChange([...v, c]); setText(""); setErr("");
  };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {options.map((s) => (
          <button type="button" key={s} onClick={() => onChange(v.includes(s) ? v.filter((x) => x !== s) : [...v, s])}
            className={`badge cursor-pointer border px-3 py-1 text-xs transition duration-200 ${v.includes(s) ? "border-brand-600 bg-brand-600 text-white shadow-soft" : "border-ink-200 bg-white text-ink-600 hover:-translate-y-0.5 hover:border-ink-400"}`}
            aria-pressed={v.includes(s)}>
            {s}
          </button>
        ))}
        {custom.map((c) => (
          <span key={c} className="badge border border-brand-600 bg-brand-600 py-1 pl-3 pr-1.5 text-xs text-white">
            {c}
            <button type="button" aria-label={`Remove ${c}`} onClick={() => onChange(v.filter((x) => x !== c))}
              className="grid h-4 w-4 place-items-center rounded-full text-white/70 hover:bg-white/20 hover:text-white">×</button>
          </span>
        ))}
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
          className={`badge cursor-pointer border border-dashed px-3 py-1 text-xs transition duration-200 ${open ? "border-ink-900 text-ink-900" : "border-ink-300 bg-white text-ink-600 hover:border-ink-500"}`}>
          {open ? "Other ▴" : "+ Other"}
        </button>
      </div>
      {open && (
        <div className="space-y-1">
          <div className="flex gap-2">
            <input className="input py-2" value={text} maxLength={40} placeholder={placeholder} aria-label="Type a cause"
              onChange={(e) => { setText(e.target.value); setErr(""); }}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
            <button type="button" className="btn-ghost shrink-0" onClick={add} disabled={!text.trim()}>Add</button>
          </div>
          {err ? <p className="text-xs text-red-700">{err}</p> : (
            <p className="text-[11px] text-ink-500">
              Up to {MAX_CUSTOM} of your own ({custom.length} added). {hint ?? "They are matched by exact wording, so also pick the closest standard sector if one fits."}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export const Err = ({ msg }) => (msg ? <p className="text-sm text-red-700">{msg}</p> : null);
export const Ok = ({ msg }) => (msg ? <p className="text-sm text-emerald-800">{msg}</p> : null);

export function Timeline({ events, render }) {
  return (
    <ol className="relative border-l border-slate-200 pl-4">
      {events.map((e) => (
        <li key={e.id || e.event_id} className="mb-3">
          <div className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full border-2 border-white bg-brand-600 ring-4 ring-brand-100" />
          {render(e)}
        </li>
      ))}
    </ol>
  );
}

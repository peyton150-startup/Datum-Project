import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Discrepancy, PlaneValue, fetchDiscrepancies, resolveDiscrepancy } from "./api";
import { ListFooter, LoadedRowsFilter, Sprite, SpriteName, Toggle, isTypingTarget } from "./arcade";
import { DiscrepancyState, DiscrepancyType, Plane } from "./enums";
import { usePagedList } from "./usePagedList";

/** Render a plane's statement so the three cases stay three cases.
 *
 * "not declared" and "null" must not both render as the string "null" -- that
 * collapse is the defect WBS 1.5.0 exists to remove, and it is felt here, in
 * front of the person deciding what to do about the drift.
 */
function planeText(pv: PlaneValue): string {
  if (pv.present === null) return "—";
  if (!pv.present) return "not stated";
  if (pv.value === null) return "null";
  return typeof pv.value === "object" ? JSON.stringify(pv.value) : String(pv.value);
}

interface TypeDisplay {
  label: string;
  meaning: string;
  sprite: SpriteName;
  colour: string;
}

const TYPE_DISPLAY: Record<string, TypeDisplay> = {
  [DiscrepancyType.FIELD]: {
    label: "FIELD DRIFT", meaning: "Both planes know this resource and disagree on a field.",
    sprite: "bee", colour: "text-yellow-300",
  },
  [DiscrepancyType.DECLARED_MISSING]: {
    label: "NEVER PROVISIONED", meaning: "Declared in intent, not found in the estate.",
    sprite: "butterfly", colour: "text-red-400",
  },
  [DiscrepancyType.DISCOVERED_UNDECLARED]: {
    label: "UNDECLARED", meaning: "Found in the estate, declared by nobody.",
    sprite: "boss", colour: "text-green-400",
  },
  [DiscrepancyType.MISSING_COMPARISON_POLICY]: {
    label: "NO POLICY", meaning: "Comparison policy needed — drift cannot be determined.",
    sprite: "boss", colour: "text-fuchsia-400",
  },
};

/** A type this build has no entry for is shown by its own name rather than
 *  dressed up as one it knows. */
function typeDisplay(discrepancyType: string): TypeDisplay {
  return TYPE_DISPLAY[discrepancyType] ?? {
    label: discrepancyType.replace(/_/g, " ").toUpperCase(), meaning: "",
    sprite: "boss", colour: "text-fuchsia-400",
  };
}

const STATE_OPTIONS = [
  { value: DiscrepancyState.OPEN, text: "OPEN" },
  { value: DiscrepancyState.RESOLVED, text: "RESOLVED" },
] as const;

function matchesFilter(d: Discrepancy, needle: string): boolean {
  const haystack = [d.kind_name, d.scope, d.name, d.field_name ?? "", d.discrepancy_type];
  return haystack.join(" ").toLowerCase().includes(needle.toLowerCase());
}

function PlanePanel({ plane, statement, isAuthoritative }: {
  plane: Plane; statement: PlaneValue; isAuthoritative: boolean;
}) {
  return (
    <div className={`border-2 p-3 ${isAuthoritative ? "border-yellow-300 bg-yellow-300/10" : "border-slate-700"}`}>
      {isAuthoritative && (
        <span data-testid="authoritative-badge"
              className="font-arcade mb-2 inline-block bg-yellow-300 px-2 py-1 text-[10px] text-black">
          {plane} — authoritative
        </span>
      )}
      <div className="text-slate-400">
        {plane}: <b data-testid={`${plane}-value`} className="text-slate-100">{planeText(statement)}</b>
      </div>
    </div>
  );
}

export function ReviewQueue({ onResolved }: { onResolved?: () => void }) {
  const [state, setState] = useState<DiscrepancyState>(DiscrepancyState.OPEN);
  const fetchPage = useCallback((offset: number) => fetchDiscrepancies(state, offset), [state]);
  const { items, count, status, error, hasMore, loadMore, removeOne } = usePagedList(fetchPage);

  const [filter, setFilter] = useState("");
  const [focus, setFocus] = useState(0);
  const [marked, setMarked] = useState<ReadonlySet<number>>(new Set());
  const [underFire, setUnderFire] = useState<ReadonlySet<number>>(new Set());
  const [resolveError, setResolveError] = useState<string | null>(null);
  const filterInput = useRef<HTMLInputElement>(null);

  const visible = useMemo(() => items.filter((d) => matchesFilter(d, filter)), [items, filter]);
  const focused = visible[Math.min(focus, visible.length - 1)];
  const isOpenView = state === DiscrepancyState.OPEN;

  useEffect(() => {
    setFocus(0);
    setMarked(new Set());
  }, [state, filter]);

  /** One request per discrepancy, in order: the API has no bulk endpoint.
   *  Stops at the first failure so what is left on screen is what is still open. */
  const resolveAll = useCallback(async (ids: number[]) => {
    setResolveError(null);
    setUnderFire(new Set(ids));
    try {
      for (const id of ids) {
        await resolveDiscrepancy(id);
        removeOne((d) => d.id === id);
        setMarked((current) => new Set([...current].filter((m) => m !== id)));
        onResolved?.();
      }
    } catch (reason) {
      setResolveError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUnderFire(new Set());
    }
  }, [removeOne, onResolved]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTypingTarget(event.target)) return;
      const last = visible.length - 1;
      if (event.key === "j") setFocus((f) => Math.min(Math.min(f, last) + 1, last));
      if (event.key === "k") setFocus((f) => Math.max(Math.min(f, last) - 1, 0));
      if (event.key === "m" && hasMore && status === "ready") loadMore();
      if (event.key === "/") {
        event.preventDefault();
        filterInput.current?.focus();
      }
      if (!isOpenView || !focused || underFire.size > 0) return;
      if (event.key === "x") {
        setMarked((current) => {
          const next = new Set(current);
          if (!next.delete(focused.id)) next.add(focused.id);
          return next;
        });
      }
      if (event.key === "r") {
        const targets = visible.filter((d) => marked.has(d.id)).map((d) => d.id);
        resolveAll(targets.length > 0 ? targets : [focused.id]);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, focused, marked, underFire, isOpenView, hasMore, status, loadMore, resolveAll]);

  return (
    <section>
      <Toggle label="Discrepancy state" options={STATE_OPTIONS} value={state} onChange={setState} />
      <p className="font-arcade mb-2 text-xs text-red-400">
        {isOpenView ? "ENEMIES REMAINING" : "ENEMIES DESTROYED"}{" "}
        <span data-testid="discrepancy-count" className="text-slate-100">{count}</span>
      </p>
      <p className="mb-4 text-xs text-slate-400">
        j / k move{isOpenView && " · x mark · r resolve focused, or every marked row"}
      </p>
      <LoadedRowsFilter value={filter} onChange={setFilter} loaded={items.length} total={count}
                        inputRef={filterInput} />
      {resolveError && (
        <p role="alert" className="mb-4 border-2 border-red-500 p-3 text-sm text-red-300">
          Resolve failed and stopped: {resolveError}
        </p>
      )}
      <ul>
        {visible.map((d) => {
          const display = typeDisplay(d.discrepancy_type);
          const isFocused = d === focused;
          return (
            <li key={d.id} aria-current={isFocused}
                className={`mb-3 border-2 p-4 ${isFocused ? "border-cyan-300" : "border-slate-800"} ${underFire.has(d.id) ? "under-fire" : ""}`}>
              <div className="mb-2 flex items-center gap-3">
                <Sprite name={isFocused ? "ship" : display.sprite}
                        className={isFocused ? "text-cyan-300" : display.colour} />
                <span className={`font-arcade text-[10px] ${display.colour}`}>{display.label}</span>
                {marked.has(d.id) && (
                  <span data-testid="marked" className="font-arcade text-[10px] text-fuchsia-400">LOCKED ON</span>
                )}
              </div>
              <div className="mb-2 text-slate-100">
                {d.kind_name} · {d.scope}/{d.name}
                {d.field_name !== null && <> · <span data-testid="field-name">{d.field_name}</span></>}
              </div>
              {display.meaning && <p className="mb-2 text-sm text-slate-400">{display.meaning}</p>}
              {d.field_name === null && d.authoritative_plane && (
                <span data-testid="authoritative-badge"
                      className="font-arcade inline-block bg-yellow-300 px-2 py-1 text-[10px] text-black">
                  {d.authoritative_plane} — authoritative
                </span>
              )}
              {d.field_name !== null && (
                <div className="grid grid-cols-2 gap-4">
                  <PlanePanel plane={Plane.DECLARED} statement={d.declared}
                              isAuthoritative={d.authoritative_plane === Plane.DECLARED} />
                  <PlanePanel plane={Plane.DISCOVERED} statement={d.discovered}
                              isAuthoritative={d.authoritative_plane === Plane.DISCOVERED} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <ListFooter status={status} error={error} hasMore={hasMore} onLoadMore={loadMore}
                  onRetry={loadMore} isEmpty={visible.length === 0}
                  emptyText={items.length > 0 ? "NO LOADED ROW MATCHES" : isOpenView ? "STAGE CLEAR" : "NOTHING RESOLVED YET"} />
    </section>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Resource, fetchResources } from "./api";
import { ListFooter, LoadedRowsFilter, Sprite, Toggle, isTypingTarget } from "./arcade";
import { Plane } from "./enums";
import { usePagedList } from "./usePagedList";

const PLANE_OPTIONS = [
  { value: Plane.DECLARED, text: "DECLARED" },
  { value: Plane.DISCOVERED, text: "DISCOVERED" },
] as const;

function matchesFilter(r: Resource, needle: string): boolean {
  return [r.kind_name, r.scope, r.name].join(" ").toLowerCase().includes(needle.toLowerCase());
}

function attributeText(value: unknown): string {
  if (value === null) return "null";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** The API identifies a resource by kind, scope and name; it issues no id. */
function resourceKey(r: Resource): string {
  return JSON.stringify([r.kind_name, r.scope, r.name]);
}

export function ResourceExplorer() {
  const [plane, setPlane] = useState<Plane>(Plane.DECLARED);
  const fetchPage = useCallback((offset: number) => fetchResources(plane, offset), [plane]);
  const { items, count, status, error, hasMore, loadMore } = usePagedList(fetchPage);

  const [filter, setFilter] = useState("");
  const [focus, setFocus] = useState(0);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const filterInput = useRef<HTMLInputElement>(null);

  const visible = useMemo(() => items.filter((r) => matchesFilter(r, filter)), [items, filter]);
  const focused = visible[Math.min(focus, visible.length - 1)];

  useEffect(() => {
    setFocus(0);
  }, [plane, filter]);

  const toggleExpanded = useCallback((key: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (isTypingTarget(event.target)) return;
      const last = visible.length - 1;
      if (event.key === "j") setFocus((f) => Math.min(Math.min(f, last) + 1, last));
      if (event.key === "k") setFocus((f) => Math.max(Math.min(f, last) - 1, 0));
      if (event.key === "m" && hasMore && status === "ready") loadMore();
      if (event.key === "Enter" && focused) toggleExpanded(resourceKey(focused));
      if (event.key === "/") {
        event.preventDefault();
        filterInput.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, focused, hasMore, status, loadMore, toggleExpanded]);

  return (
    <section>
      <Toggle label="Plane" options={PLANE_OPTIONS} value={plane} onChange={setPlane} />
      <p className="font-arcade mb-2 text-xs text-cyan-300">
        FLEET SIZE <span data-testid="resource-count" className="text-slate-100">{count}</span>
      </p>
      <p className="mb-4 text-xs text-slate-400">j / k move · enter show attributes</p>
      <LoadedRowsFilter value={filter} onChange={setFilter} loaded={items.length} total={count}
                        inputRef={filterInput} />
      <ul>
        {visible.map((r) => {
          const key = resourceKey(r);
          const isFocused = r === focused;
          const isExpanded = expanded.has(key);
          const attributes = Object.entries(r.attributes);
          return (
            <li key={key} aria-current={isFocused}
                className={`mb-2 border-2 p-3 ${isFocused ? "border-cyan-300" : "border-slate-800"}`}>
              <button aria-expanded={isExpanded} onClick={() => toggleExpanded(key)}
                      className="flex w-full items-center gap-3 text-left text-slate-100">
                <Sprite name={isFocused ? "ship" : "bee"}
                        className={isFocused ? "text-cyan-300" : "text-slate-500"} />
                <span>{r.kind_name} · {r.scope}/{r.name}</span>
                <span className="ml-auto text-xs text-slate-400">{attributes.length} attributes</span>
              </button>
              {isExpanded && (
                <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-slate-800 pt-3 text-sm">
                  {attributes.length === 0 && <dt className="text-slate-400">no attributes</dt>}
                  {attributes.map(([name, value]) => (
                    <div key={name} className="contents">
                      <dt className="text-cyan-300">{name}</dt>
                      <dd className="break-all text-slate-100">{attributeText(value)}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          );
        })}
      </ul>
      <ListFooter status={status} error={error} hasMore={hasMore} onLoadMore={loadMore}
                  onRetry={loadMore} isEmpty={visible.length === 0}
                  emptyText={items.length > 0 ? "NO LOADED ROW MATCHES" : "NO SHIPS ON THIS PLANE"} />
    </section>
  );
}

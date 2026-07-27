import { useEffect, useState } from "react";
import { Discrepancy, fetchOpenDiscrepancies, resolveDiscrepancy } from "./api";

export function ReviewQueue() {
  const [items, setItems] = useState<Discrepancy[]>([]);
  const [focus, setFocus] = useState(0);

  useEffect(() => {
    fetchOpenDiscrepancies().then(setItems);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "j") setFocus((f) => Math.min(f + 1, items.length - 1));
      if (event.key === "k") setFocus((f) => Math.max(f - 1, 0));
      if (event.key === "r" && items[focus]) {
        const id = items[focus].id;
        resolveDiscrepancy(id).then(() => {
          setItems((current) => current.filter((d) => d.id !== id));
          setFocus((f) => Math.max(0, Math.min(f, items.length - 2)));
        });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, focus]);

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="mb-4 text-xl font-semibold">Review Queue</h1>
      <p className="mb-4 text-sm text-gray-500">j / k to move, r to resolve</p>
      <ul>
        {items.map((d, index) => (
          <li key={d.id}
              className={`mb-3 rounded border p-4 ${index === focus ? "ring-2 ring-blue-500" : ""}`}>
            <div className="mb-2 font-medium">
              {d.kind_name} · {d.scope}/{d.name} ·{" "}
              <span data-testid="field-name">{d.field_name}</span>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="rounded bg-blue-50 p-3">
                <span data-testid="authoritative-badge"
                      className="mb-1 inline-block rounded bg-blue-600 px-2 text-xs text-white">
                  {d.authoritative_plane} — authoritative
                </span>
                <div>declared: <b data-testid="declared-value">{String(d.declared_value)}</b></div>
              </div>
              <div className="rounded bg-gray-50 p-3">
                <div>discovered: <b data-testid="discovered-value">{String(d.discovered_value)}</b></div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </main>
  );
}

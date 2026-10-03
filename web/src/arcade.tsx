import { RefObject } from "react";
import { ListStatus } from "./usePagedList";

/** Pixel sprites, drawn from bitmaps so each stays hand-checkable. */
const SPRITES = {
  bee: [
    "..#.....#..",
    "...#...#...",
    "..#######..",
    ".##.###.##.",
    "###########",
    "#.#######.#",
    "#.#.....#.#",
    "...##.##...",
  ],
  butterfly: [
    "#...###...#",
    "##.#####.##",
    "###########",
    "###.###.###",
    ".#########.",
    "..##...##..",
    ".##.....##.",
    "#.........#",
  ],
  boss: [
    "...#####...",
    ".#########.",
    "###########",
    "##..###..##",
    "###########",
    "..###.###..",
    ".##..#..##.",
    "##.......##",
  ],
  ship: [
    ".....#.....",
    ".....#.....",
    "....###....",
    "....###....",
    ".#.#####.#.",
    ".#########.",
    "###########",
    "##.#.#.#.##",
  ],
} as const;

export type SpriteName = keyof typeof SPRITES;

export function Sprite({ name, className = "" }: { name: SpriteName; className?: string }) {
  const rows = SPRITES[name];
  return (
    <svg viewBox={`0 0 ${rows[0].length} ${rows.length}`} aria-hidden="true"
         shapeRendering="crispEdges" className={`inline-block h-6 w-8 fill-current ${className}`}>
      {rows.flatMap((row, y) =>
        [...row].map((cell, x) =>
          cell === "#" ? <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} /> : null))}
    </svg>
  );
}

export function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
}

/** Narrows the rows already loaded. The API has no filter parameters, so the
 *  label says what is being searched rather than implying the whole list. */
export function LoadedRowsFilter({ value, onChange, loaded, total, inputRef }: {
  value: string;
  onChange: (value: string) => void;
  loaded: number;
  total: number;
  inputRef: RefObject<HTMLInputElement>;
}) {
  return (
    <label className="mb-4 block text-xs text-cyan-300">
      <span className="font-arcade">FILTER</span>{" "}
      <span className="text-slate-400">
        searches the {loaded} of {total} rows loaded — press / to focus
      </span>
      <input ref={inputRef} value={value} onChange={(e) => onChange(e.target.value)}
             onKeyDown={(e) => { if (e.key === "Escape") e.currentTarget.blur(); }}
             placeholder="kind, scope, name…"
             className="mt-1 block w-full border-2 border-cyan-700 bg-black px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-300" />
    </label>
  );
}

export function ListFooter({ status, error, hasMore, onLoadMore, onRetry, emptyText, isEmpty }: {
  status: ListStatus;
  error: string | null;
  hasMore: boolean;
  onLoadMore: () => void;
  onRetry: () => void;
  emptyText: string;
  isEmpty: boolean;
}) {
  if (status === "loading") {
    return <p className="font-arcade blink text-xs text-yellow-300">LOADING…</p>;
  }
  if (status === "error") {
    return (
      <div role="alert" className="border-2 border-red-500 p-3 text-sm text-red-300">
        <p className="font-arcade mb-2 text-xs text-red-400">CONNECTION LOST</p>
        <p className="mb-2">{error}</p>
        <button onClick={onRetry} className="arcade-button">RETRY</button>
      </div>
    );
  }
  return (
    <>
      {isEmpty && <p className="font-arcade mb-3 text-xs text-green-400">{emptyText}</p>}
      {hasMore && <button onClick={onLoadMore} className="arcade-button">LOAD MORE (m)</button>}
    </>
  );
}

export function Toggle<T extends string>({ label, options, value, onChange }: {
  label: string;
  options: readonly { value: T; text: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="mb-4 flex gap-2">
      {options.map((option) => (
        <button key={option.value} aria-pressed={option.value === value}
                onClick={() => onChange(option.value)}
                className={`arcade-button ${option.value === value ? "arcade-button-on" : ""}`}>
          {option.text}
        </button>
      ))}
    </div>
  );
}

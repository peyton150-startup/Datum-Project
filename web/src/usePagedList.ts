import { useCallback, useEffect, useRef, useState } from "react";
import { Page } from "./api";

export type ListStatus = "loading" | "ready" | "error";

/** Hold the loaded windows of one server-side list.
 *
 * `fetchPage` identifies the list: when it changes, the rows are dropped and
 * the first window is fetched again, so callers must keep it stable with
 * `useCallback`. A response that arrives after the list changed is discarded.
 */
export function usePagedList<T>(fetchPage: (offset: number) => Promise<Page<T>>) {
  const [items, setItems] = useState<T[]>([]);
  const [count, setCount] = useState(0);
  const [status, setStatus] = useState<ListStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  const load = useCallback((offset: number) => {
    const requested = generation.current;
    setStatus("loading");
    setError(null);
    fetchPage(offset).then(
      (page) => {
        if (requested !== generation.current) return;
        setItems((current) => (offset === 0 ? page.items : [...current, ...page.items]));
        setCount(page.count);
        setStatus("ready");
      },
      (reason: unknown) => {
        if (requested !== generation.current) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setStatus("error");
      },
    );
  }, [fetchPage]);

  useEffect(() => {
    generation.current += 1;
    setItems([]);
    setCount(0);
    load(0);
  }, [load]);

  const loadMore = useCallback(() => load(items.length), [load, items.length]);

  /** Drop one row the server no longer lists, keeping `count` in step so the
   *  next window's offset still lines up with what the server holds. */
  const removeOne = useCallback((isGone: (item: T) => boolean) => {
    setItems((current) => current.filter((item) => !isGone(item)));
    setCount((n) => n - 1);
  }, []);

  return { items, count, status, error, hasMore: items.length < count, loadMore, removeOne };
}

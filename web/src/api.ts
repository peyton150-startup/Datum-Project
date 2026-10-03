import { DiscrepancyState, Plane } from "./enums";

/** One plane's statement about a field.
 *
 * `present` and `value` are separate because `null` is already taken: a field
 * intent never mentions and a field intent sets to null are different claims.
 * `present` is null only for rows recorded before the distinction existed.
 */
export interface PlaneValue {
  present: boolean | null;
  value: unknown;
}

export interface Discrepancy {
  id: number;
  discrepancy_type: string;
  kind_name: string;
  scope: string;
  name: string;
  field_name: string | null;
  declared: PlaneValue;
  discovered: PlaneValue;
  authoritative_plane: string | null;
  state: string;
}

export interface Resource {
  name: string;
  scope: string;
  kind_name: string;
  attributes: Record<string, unknown>;
}

/** One window of a list, plus how many rows the whole list has.
 *
 * The page size is the server's and is deliberately not repeated here: a
 * caller asks for the next window by passing how many rows it already holds,
 * and knows it is finished when it holds `count` of them.
 */
export interface Page<T> {
  count: number;
  items: T[];
}

export class ApiError extends Error {
  constructor(public readonly status: number, path: string) {
    super(`${path} answered ${status}`);
    this.name = "ApiError";
  }
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(path, init);
  if (!res.ok) throw new ApiError(res.status, path);
  return res.json();
}

export function fetchDiscrepancies(
  state: DiscrepancyState, offset = 0,
): Promise<Page<Discrepancy>> {
  const query = new URLSearchParams({ state, offset: String(offset) });
  return request(`/api/discrepancies?${query}`) as Promise<Page<Discrepancy>>;
}

export function fetchResources(plane: Plane, offset = 0): Promise<Page<Resource>> {
  const query = new URLSearchParams({ plane, offset: String(offset) });
  return request(`/api/resources?${query}`) as Promise<Page<Resource>>;
}

export async function resolveDiscrepancy(id: number): Promise<void> {
  await request(`/api/discrepancies/${id}/resolve`, { method: "POST" });
}

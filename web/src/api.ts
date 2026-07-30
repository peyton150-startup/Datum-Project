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
  authoritative_plane: string;
  state: string;
}

export async function fetchOpenDiscrepancies(): Promise<Discrepancy[]> {
  const res = await fetch("/api/discrepancies?state=open");
  const body = await res.json();
  return body.items as Discrepancy[];
}

export async function resolveDiscrepancy(id: number): Promise<void> {
  await fetch(`/api/discrepancies/${id}/resolve`, { method: "POST" });
}

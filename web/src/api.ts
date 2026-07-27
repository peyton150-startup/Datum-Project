export interface Discrepancy {
  id: number;
  discrepancy_type: string;
  kind_name: string;
  scope: string;
  name: string;
  field_name: string | null;
  declared_value: unknown;
  discovered_value: unknown;
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

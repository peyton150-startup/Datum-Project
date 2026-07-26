# ADR-001: Resource kinds are schema-defined data, not a model per kind

**Status:** Accepted

**Context.** Datum inventories a heterogeneous estate: Kubernetes workloads, cloud instances, networks, certificates, and more to come. Each kind has a different attribute set. The system's whole premise is that adding a new kind should be cheap, because an inventory that requires an engineer and a deploy to track a new resource type is the inventory that goes stale.

**Options.**
1. A Django model per kind. Native ORM, typed fields, IDE completion, a migration and a deploy for every new kind.
2. Kinds are rows in a table; a kind's attribute schema is data; resources store attributes against that schema. A new kind is a config change, no migration.

**Decision.** Option 2. Kinds are data. A `Kind` record carries a name and an attribute schema. Resources reference their kind and hold attributes validated against it.

**Consequences.**
- Adding a kind is a data operation, which is the central product differentiator and the thing that separates Datum from a model-per-device tool.
- The cost moves from migrations to validation: Datum owns the code that checks a resource against its kind's schema, because the database no longer does it by column type.
- Querying kind-specific fields goes through JSONB rather than columns. ADR-005 governs when a field is promoted to a typed column.
- The shared, every-kind fields still live in real columns (see ADR-005), so the common queries stay fast and constrained.

**Cost to reverse.** High. Every resource row and every query assumes the data-driven shape. Reversing means a model per kind and a migration backfill for all existing data. Treat as effectively permanent.

# ADR-005: Typed-column versus JSONB split

**Status:** Accepted (decided; full writeup folded into DESIGN.md section 5)

**Decision.**
- A field earns a typed column only if a query **filters, sorts, joins, or constrains** on it. Otherwise it stays in JSONB.
- **Global core, not per-kind columns.** Every resource of every kind shares one set of typed columns: `tenant_id`, `kind`, `name`, `scope`, `provider_id`, timestamps, and the match/state foreign keys. Kind-specific fields stay in JSONB.
- **Manual promotion, triggered by evidence.** Promoting a field is a deliberate migration: add the column, backfill, repoint the query.
- **Promote, never duplicate.** Once a field becomes a column it leaves JSONB entirely. A field stored in two places drifts — the exact bug this product exists to catch.

**Cost to reverse.** High. Every resource row and query assumes this shape. See DESIGN.md section 5.

# ADR-007: Read-only toward the estate, permanently

**Status:** Accepted (stub — a standing constraint, not a phase)

**Decision.** No Datum code path writes to, provisions, or remediates any provider resource. Collectors read only. This removes the entire class of concerns around apply ordering, rollback, and blast radius, and makes correctness a question of representation rather than execution.

**Consequences.** Any write-back or remediation proposal is a new project with its own scope, not a phase of this one. A collector that mutates the estate, or writes to the declared plane, is wrong by definition.

**Cost to reverse.** Not a reversal — adding write-back is a different product.

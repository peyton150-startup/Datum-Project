# ADR-008: Error handling uses a barricade, validate at the edge and trust inside

**Status:** Accepted

**Context.** Data enters Datum from three untrusted sources: provider APIs, Git document contents, and HTTP requests. Left undecided, error handling is chosen per module, and a codebase ends up with several incompatible contracts and validation scattered everywhere or nowhere. The decision must be made once, at the architecture level, because it is inherited by every module boundary and cannot be retrofitted cheaply.

**Options.**
1. Validate everywhere. Every function defends against every input. Safe-feeling, but doubles the code and multiplies the places a check can be wrong.
2. A barricade. Validate hard at the system's edges, convert external data to trusted domain types there, and let interior code assume its inputs are valid, using assertions for what should be impossible.

**Decision.** Option 2. There is a dirty zone at the boundary and a clean zone inside.

- **Dirty zone:** provider responses, Git document contents, HTTP request bodies. Validate everything. Convert to domain types immediately. A raw provider dict never travels inward.
- **Clean zone:** `graph`, `reconcile`, `workflow`. Inputs are already valid. Use assertions for bug conditions; handle only what can legitimately occur at runtime.

**Consequences.**
- Interior code is smaller and clearer because it does not re-check what the barricade guaranteed.
- Errors surface at the boundary, where the context needed to diagnose them still exists, instead of deep in the kernel.
- The boundary must be unambiguous and written down, because the whole scheme fails if code disagrees about where it sits. The module responsibilities in DESIGN.md section 3 define it.
- Exceptions crossing a module boundary carry that module's abstraction; lower-level exceptions are wrapped, never leaked.
- Assertions may be compiled out, so they never contain side effects. Anything that must run in production is error handling, not an assertion.

**Cost to reverse.** High in effect. Once interior code trusts its inputs, retrofitting per-function validation means auditing every routine. The boundary location can be adjusted; abandoning the barricade cannot, cheaply.

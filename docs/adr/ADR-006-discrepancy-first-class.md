# ADR-006: Discrepancy as a first-class entity rather than a changelog

**Status:** Accepted (stub — full writeup to land with the workflow code in phase 4)

**Decision.** A difference between the declared and discovered planes is a first-class `Discrepancy` record with its own lifecycle (state, assignee, suppression reason, expiry), not a changelog entry recorded after the fact. This is a core NetBox differentiator: the gap between intent and reality is the primary object of the system, stored and worked, never silently closed.

**Cost to reverse.** High. The review queue, lifecycle, and audit trail all assume the entity exists.

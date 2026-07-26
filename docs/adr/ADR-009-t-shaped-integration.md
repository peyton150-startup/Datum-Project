# ADR-009: T-shaped integration, vertical slice inside phase 1

**Status:** Accepted (stub — full writeup folded into PROJECT_PLAN.md construction practices)

**Decision.** Integration is T-shaped, then feature-oriented. The vertical bar of the T comes first: one kind, declared in Git, discovered by one collector, matched, diffed, and reviewed in the UI — end to end, inside phase 1. Then breadth is added one complete feature at a time onto that skeleton.

**Rationale.** The phase order reads layer by layer; following it literally would build the whole graph layer, then the whole intent layer, then discover at phase 4 that the model is wrong. The T-slice validates the riskiest architectural assumption at the smallest possible cost.

**Cost to reverse.** Low as a process choice, but abandoning it re-introduces the big-bang integration risk it exists to remove.

# ADR-004: Intent lives in Git and is projected into the database

**Status:** Accepted

**Context.** Datum holds two kinds of state: what should be true (intent) and what is true (discovered). Intent is authored by humans. The question is where intent's system of record lives.

**Options.**
1. Intent is edited in the web UI and stored in the database as the origin. Simple, one store, immediate edits. This is how NetBox works.
2. Intent is authored as declarative documents in a Git repository, reviewed by pull request, and projected into the database on push. The database is a disposable projection; Git is the origin.

**Decision.** Option 2. Git is the source of truth for intent. The database holds a rebuildable projection of a specific commit.

**Consequences.**
- Free version history, blame, review, and rollback for intent, using tooling every engineer already knows. This is the second pillar of the NetBox differentiation and it is what makes ADR-001 fully useful, since kind definitions can live in Git too.
- Datum owns an ingestion pipeline: webhook or poll, validate, project. That is real work and it is on the critical path.
- The database can always be rebuilt from commits, a strong recovery and auditability property.
- Intent edits require a Git round trip, so casual point-and-click editing is deliberately not supported. Accepted, because the review-gated workflow is the point.
- Every declared resource traces to the commit that declared it.

**Cost to reverse.** High. Ingestion, projection, and the immutable-revision model all assume Git as origin. Reversing means making the database authoritative and building UI editing, which is a different product.

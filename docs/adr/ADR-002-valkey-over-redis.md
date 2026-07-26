# ADR-002: Valkey over Redis for broker and cache

**Status:** Accepted (stub — full writeup to land with the code that uses it)

**Decision.** Use Valkey (BSD) as the Celery broker and cache. Redis relicensed away from BSD in 2024; Valkey is the OSI-approved fork that keeps Datum's "OSI-approved open source, free at the scale used" constraint intact.

**Cost to reverse.** Low. Broker/cache is behind a URL; swapping back is configuration, not code.

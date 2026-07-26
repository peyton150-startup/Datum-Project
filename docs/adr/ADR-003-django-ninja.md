# ADR-003: django-ninja over Django REST Framework

**Status:** Accepted (stub — full writeup to land with the API code)

**Decision.** Use django-ninja (MIT) for the read-only REST API. Its Pydantic-typed schemas generate an OpenAPI spec that pairs cleanly with the TypeScript client, keeping the system typed end to end — which is part of the point of the project.

**Cost to reverse.** Medium. Endpoint definitions would be rewritten, but the read model and business logic sit behind the API layer and are unaffected.

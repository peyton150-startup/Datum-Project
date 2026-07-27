from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import NinjaAPI

from datum.api.schemas import PageDiscrepancies, PageResources
from datum.discovery.models import DiscoveredResource
from datum.enums import DiscrepancyState, Plane
from datum.graph.models import DeclaredResource
from datum.reconcile.models import Discrepancy

api = NinjaAPI(title="Datum", version="1.0.0")
PAGE_SIZE = 50
TENANT = settings.DEFAULT_TENANT_ID


@api.get("/resources", response=PageResources)
def list_resources(request, plane: str = Plane.DECLARED.value, offset: int = 0):
    query = _declared_query() if plane == Plane.DECLARED.value else _discovered_query()
    window = query[offset : offset + PAGE_SIZE]
    items = [
        {
            "name": r.name,
            "scope": r.scope,
            "kind_name": r.kind.name,
            "attributes": r.attributes,
        }
        for r in window
    ]
    return {"count": query.count(), "items": items}


def _declared_query():
    """Full-rebuild projection (DESIGN 10) keeps every revision's rows, so the
    declared plane only means anything read through the active revision. A
    reader that omits this filter sees every revision at once."""
    return (
        DeclaredResource.objects.filter(tenant_id=TENANT, revision__is_active=True)
        .select_related("kind")
        .order_by("scope", "name")
    )


def _discovered_query():
    return (
        DiscoveredResource.objects.filter(tenant_id=TENANT)
        .select_related("kind")
        .order_by("scope", "name")
    )


@api.get("/discrepancies", response=PageDiscrepancies)
def list_discrepancies(request, state: str = DiscrepancyState.OPEN.value, offset: int = 0):
    query = Discrepancy.objects.filter(tenant_id=TENANT, state=state).order_by(
        "kind_name", "scope", "name", "field_name"
    )
    window = query[offset : offset + PAGE_SIZE]
    items = [_serialize(d) for d in window]
    return {"count": query.count(), "items": items}


@api.post("/discrepancies/{discrepancy_id}/resolve")
def resolve_discrepancy(request, discrepancy_id: int):
    discrepancy = get_object_or_404(Discrepancy, id=discrepancy_id, tenant_id=TENANT)
    discrepancy.state = DiscrepancyState.RESOLVED
    discrepancy.resolved_at = timezone.now()
    discrepancy.save(update_fields=["state", "resolved_at"])
    return {"id": discrepancy.id, "state": discrepancy.state}


def _serialize(d: Discrepancy) -> dict:
    return {
        "id": d.id,
        "discrepancy_type": d.discrepancy_type,
        "kind_name": d.kind_name,
        "scope": d.scope,
        "name": d.name,
        "field_name": d.field_name,
        "declared_value": d.declared_value,
        "discovered_value": d.discovered_value,
        "authoritative_plane": d.authoritative_plane,
        "state": d.state,
    }

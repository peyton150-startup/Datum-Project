from ninja import Schema


class ResourceOut(Schema):
    name: str
    scope: str
    kind_name: str
    attributes: dict


class DiscrepancyOut(Schema):
    id: int
    discrepancy_type: str
    kind_name: str
    scope: str
    name: str
    field_name: str | None
    declared_value: object | None
    discovered_value: object | None
    authoritative_plane: str
    state: str


class PageResources(Schema):
    count: int
    items: list[ResourceOut]


class PageDiscrepancies(Schema):
    count: int
    items: list[DiscrepancyOut]

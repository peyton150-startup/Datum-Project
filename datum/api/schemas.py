from ninja import Schema


class ResourceOut(Schema):
    name: str
    scope: str
    kind_name: str
    attributes: dict


class PlaneValueOut(Schema):
    """One plane's statement about a field: whether it says anything, and what.

    `present` and `value` are separate because `null` is already taken. A field
    intent never mentions and a field intent sets to null are different claims,
    and a consumer that sees one `null` for both cannot tell them apart.

    `present` is nullable only for rows recorded before WBS 1.5.0, where the
    distinction was never determined. New rows always state it.
    """

    present: bool | None
    value: object | None


class DiscrepancyOut(Schema):
    id: int
    discrepancy_type: str
    kind_name: str
    scope: str
    name: str
    field_name: str | None
    declared: PlaneValueOut
    discovered: PlaneValueOut
    authoritative_plane: str
    state: str


class PageResources(Schema):
    count: int
    items: list[ResourceOut]


class PageDiscrepancies(Schema):
    count: int
    items: list[DiscrepancyOut]

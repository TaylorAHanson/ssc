from app.db.discover_domain import DiscoverDomainModel


def add_domain(session, tag_key, parent=None, draft=False, **fields):
    """Persist a cached Discover domain; ``parent`` is the parent's model for a subdomain."""
    row = DiscoverDomainModel(
        id=fields.pop("id", f"dom-{tag_key}"),
        tag_key=tag_key,
        parent_id=parent.id if parent is not None else None,
        draft=draft,
        **fields,
    )
    session.add(row)
    session.flush()
    return row


def add_domains(session):
    """The domains the Discover tests share: Supply Chain (with Planning), Finance (with Revenue)."""
    sc = add_domain(session, "Supply Chain", icon={"name": "TRUCK_TRAILER", "color": "#137DAE"},
                    subtitle="Plan, source and deliver")
    fin = add_domain(session, "Finance")
    add_domain(session, "Supply Chain/Planning & Forecasting", parent=sc)
    add_domain(session, "Finance/Revenue & Margin", parent=fin)
    return sc, fin

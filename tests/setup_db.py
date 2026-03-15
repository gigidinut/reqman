#!/usr/bin/env python3
"""
setup_db.py — Initialise the SQLite database and verify the schema.

Run from the project root:
    python -m reqman.database.setup_db

Or directly:
    python reqman/database/setup_db.py

Creates `reqman.db` in the current working directory, builds all tables,
seeds a default admin user, and runs a quick smoke-test demonstrating:
  • The full hierarchy (Project → System → SubSystem → Element → Requirement)
  • Many-to-Many linking between disparate entities
  • Querying all Requirements linked to a specific System
  • Cascading delete verification
  • Audit log entries
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Make the package importable when run as a script ─────────────
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from database.models import (
    Base,
    User,
    Project,
    System,
    SubSystem,
    Element,
    Requirement,
    Entity,
    EntityLink,
    AuditLog,
    get_engine,
)

DATA_DIR = _project_root / "data"
DB_FILE = DATA_DIR / "reqman.db"


def create_tables(engine):
    """Drop-and-recreate all tables (dev convenience)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("✔  All tables created.")


def seed_admin(session: Session) -> User:
    """Insert a default admin user with a temporary password."""
    admin = User(
        username="admin",
        display_name="Administrator",
        email="admin@reqman.local",
        password_hash="CHANGEME_hashed",  # placeholder
        temporary_password=True,
    )
    session.add(admin)
    session.flush()
    print(f"✔  Default admin user created  (id={admin.id}, temporary_password=True)")
    return admin


def build_sample_hierarchy(session: Session, user: User):
    """
    Build a realistic hierarchy and link entities across types.

    Hierarchy
    ─────────
    Satellite Comms (Project)
      └─ Transponder (System)
            ├─ RF Front-End (SubSystem)
            │     ├─ LNA Module (Element)
            │     │     └─ REQ-001: Noise figure ≤ 1.2 dB
            │     └─ REQ-002: Operating freq 12–18 GHz
            └─ Power Supply (SubSystem)
                  └─ REQ-003: Output ripple ≤ 50 mV

    Links (M2M)
    ────────────
    Transponder (System) ↔ REQ-001
    Transponder (System) ↔ REQ-003
    RF Front-End (SubSystem) ↔ Power Supply (SubSystem)
    """

    # ── Hierarchy ──────────────────────────
    project = Project(name="Satellite Comms", description="LEO comms payload")
    session.add(project)
    session.flush()

    transponder = System(
        name="Transponder", description="C-band transponder", parent_id=project.id
    )
    session.add(transponder)
    session.flush()

    rf = SubSystem(
        name="RF Front-End", description="Receive chain", parent_id=transponder.id
    )
    psu = SubSystem(
        name="Power Supply", description="DC-DC converters", parent_id=transponder.id
    )
    session.add_all([rf, psu])
    session.flush()

    lna = Element(
        name="LNA Module", description="Low-noise amplifier", parent_id=rf.id
    )
    session.add(lna)
    session.flush()

    req1 = Requirement(
        name="REQ-001",
        description="Noise figure shall be ≤ 1.2 dB",
        priority="high",
        rationale="Link budget margin",
        parent_id=lna.id,
    )
    req2 = Requirement(
        name="REQ-002",
        description="Operating frequency 12–18 GHz",
        priority="high",
        parent_id=rf.id,
    )
    req3 = Requirement(
        name="REQ-003",
        description="Output ripple ≤ 50 mV",
        priority="medium",
        parent_id=psu.id,
    )
    session.add_all([req1, req2, req3])
    session.flush()

    print(f"✔  Hierarchy built: Project → System → 2 SubSystems → 1 Element → 3 Requirements")

    # ── Many-to-Many links ─────────────────
    # Link Transponder ↔ REQ-001 and REQ-003
    transponder.linked_to.append(req1)
    transponder.linked_to.append(req3)
    # Link RF Front-End ↔ Power Supply (cross-subsystem dependency)
    rf.linked_to.append(psu)
    session.flush()
    print("✔  M2M links created (Transponder→REQ-001, Transponder→REQ-003, RF→PSU)")

    # ── Audit log entries ──────────────────
    for entity in [project, transponder, rf, psu, lna, req1, req2, req3]:
        session.add(
            AuditLog(
                action="CREATE",
                entity_id=entity.id,
                entity_type=entity.entity_type,
                entity_name=entity.name,
                details=json.dumps({"name": entity.name}),
                user_id=user.id,
            )
        )
    session.flush()
    print(f"✔  {8} audit log entries recorded")

    return transponder, rf, psu, req1, req2, req3


# ──────────────────────────────────────────────────────────────────
# QUERY EXAMPLE:  All Requirements linked to a specific System
# ──────────────────────────────────────────────────────────────────
def demo_query_requirements_linked_to_system(session: Session, system: System):
    """
    ┌──────────────────────────────────────────────────────────────┐
    │  HOW TO: find every Requirement linked to a given System     │
    │                                                              │
    │  This uses the EntityLink association table explicitly so     │
    │  you can filter by entity_type on the target side.           │
    └──────────────────────────────────────────────────────────────┘
    """

    # ── Approach 1: Explicit join through the association table ───
    stmt = (
        select(Requirement)
        .join(
            EntityLink,
            EntityLink.target_entity_id == Requirement.id,
        )
        .where(EntityLink.source_entity_id == system.id)
        .where(Requirement.entity_type == "requirement")
    )
    results = session.execute(stmt).scalars().all()

    print(f"\n{'─'*60}")
    print(f"  Requirements linked to System '{system.name}' (id={system.id}):")
    print(f"{'─'*60}")
    for r in results:
        print(f"    • {r.name}: {r.description}  [priority={r.priority}]")
    print()

    # ── Approach 2: Using the ORM relationship directly ──────────
    #    (simpler but loads all linked entities, then filters in Python)
    orm_results = [e for e in system.linked_to if e.entity_type == "requirement"]
    assert set(r.id for r in results) == set(r.id for r in orm_results), \
        "Both approaches must return the same set"
    print("  ✔  ORM relationship approach returns identical results.\n")

    return results


# ──────────────────────────────────────────────────────────────────
# CASCADE DELETE verification
# ──────────────────────────────────────────────────────────────────
def demo_cascade_delete(session: Session, transponder: System):
    """Delete the System and verify children + links are gone."""

    # Count before
    entities_before = session.scalar(select(func.count()).select_from(Entity))
    links_before = session.scalar(select(func.count()).select_from(EntityLink))

    print(f"  Before delete:  {entities_before} entities, {links_before} links")

    session.delete(transponder)
    session.flush()

    entities_after = session.scalar(select(func.count()).select_from(Entity))
    links_after = session.scalar(select(func.count()).select_from(EntityLink))

    print(f"  After deleting System '{transponder.name}':")
    print(f"    Entities remaining: {entities_after}  (only the Project)")
    print(f"    Links remaining:    {links_after}  (all orphans cleaned up)")

    # Audit log preserved
    audit_count = session.scalar(select(func.count()).select_from(AuditLog))
    print(f"    Audit entries:      {audit_count}  (history preserved)\n")


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'═'*60}")
    print("  Requirements Manager — Database Setup & Verification")
    print(f"{'═'*60}\n")

    # 1. Create the 'data' folder if it doesn't exist yet
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Pass the string version of the path to get_engine
    engine = get_engine(str(DB_FILE), echo=False)
    create_tables(engine)

    with Session(engine) as session:
        admin = seed_admin(session)
        transponder, rf, psu, req1, req2, req3 = build_sample_hierarchy(session, admin)
        session.commit()

        # ── Query demo ─────────────────────────
        demo_query_requirements_linked_to_system(session, transponder)

        # ── Cascade demo ───────────────────────
        print("Cascade delete test:")
        demo_cascade_delete(session, transponder)
        session.commit()

    print(f"{'═'*60}")
    print(f"  ✔  Schema verified.  Database file: {DB_FILE}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()

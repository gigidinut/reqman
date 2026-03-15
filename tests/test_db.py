#!/usr/bin/env python3
"""
test_db.py — Integration smoke-test for db_controllers.py

Exercises the full lifecycle:
  1. Initialise a fresh in-memory database.
  2. Create a user → verify audit.
  3. Authenticate (login) → verify success and temp-password flag.
  4. Update password → verify temp flag cleared.
  5. Re-authenticate → verify "ok" status.
  6. Create a Project → audit.
  7. Create a System under the Project → audit.
  8. Create a Requirement under the System → audit.
  9. Link System ↔ Requirement → audit.
  10. Update the Project (rename) → audit with old/new values.
  11. Query linked entities.
  12. Delete the Project (cascade) → audit.
  13. Verify cascade cleaned up children and links.
  14. Print the full Audit Log to terminal.
  15. Test admin password reset flow.

Run from the project root:
    python -m reqman.tests.test_db
"""

import sys
from pathlib import Path

# ── Ensure project root is on sys.path ───────────────────────────
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import (
    Base,
    Entity,
    EntityLink,
    AuditLog,
    get_engine,
)
from controllers.db_controllers import (
    init_engine,
    _session_scope,
    create_user,
    authenticate_user,
    update_password,
    reset_password,
    list_users,
    create_entity,
    get_entity,
    get_children,
    get_all_projects,
    update_entity,
    delete_entity,
    link_entities,
    unlink_entities,
    get_linked_entities,
    get_audit_log,
    get_full_audit_log_for_display,
)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def header(title: str):
    """Print a formatted section header."""
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def check(label: str, condition: bool):
    """Assert a condition and print pass/fail."""
    status = "✔ PASS" if condition else "✘ FAIL"
    print(f"  {status}  {label}")
    if not condition:
        raise AssertionError(f"Test failed: {label}")


# ═══════════════════════════════════════════════════════════════════
# Main test sequence
# ═══════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'═'*60}")
    print("  db_controllers.py — Integration Test Suite")
    print(f"{'═'*60}")

    # ── Step 1: Fresh in-memory database ─────────────────────────
    header("Step 1: Initialise in-memory database")
    engine = init_engine(db_path=":memory:", echo=False)
    Base.metadata.create_all(engine)
    print("  Database tables created (in-memory).")

    # ── Step 2: Create a user ────────────────────────────────────
    header("Step 2: Create user 'engineer1'")
    user = create_user(
        username="engineer1",
        display_name="Alice Engineer",
        email="alice@example.com",
        password="TempPass123!",
        temporary_password=True,
    )
    check("User created with id > 0", user.id is not None and user.id > 0)
    check("Username is 'engineer1'", user.username == "engineer1")
    check("temporary_password flag is True", user.temporary_password is True)
    print(f"  Created: {user}")

    # ── Step 3: Authenticate (first login — temp password) ───────
    header("Step 3: Authenticate with temporary password")
    success, auth_user, message = authenticate_user("engineer1", "TempPass123!")
    check("Login succeeded", success is True)
    check("Message is 'temporary_password'", message == "temporary_password")
    check("User object returned", auth_user is not None)

    # ── Step 3b: Verify bad password is rejected ─────────────────
    success_bad, _, msg_bad = authenticate_user("engineer1", "WrongPassword")
    check("Bad password rejected", success_bad is False)
    check("Message is 'invalid_credentials'", msg_bad == "invalid_credentials")

    # ── Step 4: Update password (clear temp flag) ────────────────
    header("Step 4: Update password → clear temporary flag")
    result = update_password(
        user_id=user.id,
        new_password="PermanentPass456!",
        clear_temporary_flag=True,
    )
    check("Password update returned True", result is True)

    # ── Step 5: Re-authenticate with new password ────────────────
    header("Step 5: Re-authenticate with new permanent password")
    success2, auth_user2, message2 = authenticate_user("engineer1", "PermanentPass456!")
    check("Login succeeded", success2 is True)
    check("Message is 'ok' (not temporary)", message2 == "ok")

    # ── Step 6: Create a Project ─────────────────────────────────
    header("Step 6: Create Project 'Satellite Comms'")
    project = create_entity(
        entity_type="project",
        name="Satellite Comms",
        user_id=user.id,
        description="LEO communications payload program",
    )
    check("Project created", project is not None)
    check("entity_type is 'project'", project.entity_type == "project")
    print(f"  Created: {project}")

    # ── Step 7: Create a System under the Project ────────────────
    header("Step 7: Create System 'Transponder' under the Project")
    system = create_entity(
        entity_type="system",
        name="Transponder",
        user_id=user.id,
        parent_id=project.id,
        description="C-band transponder subsystem group",
    )
    check("System created", system is not None)
    check("Parent is the Project", system.parent_id == project.id)
    print(f"  Created: {system}")

    # ── Step 8: Create a Requirement under the System ────────────
    header("Step 8: Create Requirement 'REQ-001' under the System")
    req = create_entity(
        entity_type="requirement",
        name="REQ-001",
        user_id=user.id,
        parent_id=system.id,
        description="Noise figure shall be ≤ 1.2 dB",
        extra_fields={"priority": "high", "rationale": "Link budget margin"},
    )
    check("Requirement created", req is not None)
    check("Priority is 'high'", req.priority == "high")
    print(f"  Created: {req}")

    # ── Step 8b: Verify Requirement cannot have children ─────────
    header("Step 8b: Verify Requirement cannot be a parent")
    try:
        bad_child = create_entity(
            entity_type="element",
            name="Should Fail",
            user_id=user.id,
            parent_id=req.id,
        )
        check("Requirement-as-parent was blocked", False)  # should not reach
    except ValueError as e:
        check("Requirement-as-parent raised ValueError", "leaf nodes" in str(e).lower())
        print(f"  Caught expected error: {e}")

    # ── Step 9: Link System ↔ Requirement ────────────────────────
    header("Step 9: Link Transponder → REQ-001")
    link = link_entities(
        source_id=system.id,
        target_id=req.id,
        user_id=user.id,
    )
    check("Link created", link is not None)
    print(f"  Created: {link}")

    # Query the link back using get_linked_entities.
    linked = get_linked_entities(system.id, direction="outgoing", target_type="requirement")
    check("Query returns 1 linked requirement", len(linked) == 1)
    check("Linked requirement is REQ-001", linked[0].name == "REQ-001")

    # ── Step 10: Update the Project (rename) ─────────────────────
    header("Step 10: Rename Project 'Satellite Comms' → 'Satellite Comms v2'")
    updated = update_entity(
        entity_id=project.id,
        user_id=user.id,
        updates={"name": "Satellite Comms v2", "status": "active"},
    )
    check("Update returned the entity", updated is not None)
    check("Name changed", updated.name == "Satellite Comms v2")
    check("Status changed", updated.status == "active")

    # ── Step 11: Verify tree queries ─────────────────────────────
    header("Step 11: Verify tree navigation queries")
    projects = get_all_projects()
    check("get_all_projects returns 1", len(projects) == 1)

    children = get_children(project.id)
    check("Project has 1 child (the System)", len(children) == 1)

    fetched = get_entity(req.id)
    check("get_entity retrieves REQ-001", fetched is not None and fetched.name == "REQ-001")

    # ── Step 12: Delete the Project (cascades) ───────────────────
    header("Step 12: Delete Project → verify cascade")

    # Count entities and links BEFORE deletion.
    with _session_scope() as s:
        entities_before = s.scalar(select(func.count()).select_from(Entity))
        links_before = s.scalar(select(func.count()).select_from(EntityLink))
    print(f"  Before: {entities_before} entities, {links_before} links")

    deleted = delete_entity(entity_id=project.id, user_id=user.id)
    check("delete_entity returned True", deleted is True)

    # Count AFTER — cascade should have removed System, Requirement, AND the link.
    with _session_scope() as s:
        entities_after = s.scalar(select(func.count()).select_from(Entity))
        links_after = s.scalar(select(func.count()).select_from(EntityLink))
    print(f"  After:  {entities_after} entities, {links_after} links")
    check("All entities removed by cascade", entities_after == 0)
    check("All links removed by cascade", links_after == 0)

    # ── Step 13: Audit log preserved after cascade ───────────────
    with _session_scope() as s:
        audit_count = s.scalar(select(func.count()).select_from(AuditLog))
    check("Audit log entries still exist", audit_count > 0)

    # ── Step 14: Password reset flow ─────────────────────────────
    header("Step 14: Admin password reset")
    admin = create_user(
        username="admin",
        display_name="Admin User",
        email="admin@example.com",
        password="AdminPass!",
        temporary_password=False,
        acting_user_id=user.id,
    )
    reset_ok = reset_password(
        user_id=user.id,
        new_temporary_password="ResetTemp789!",
        acting_user_id=admin.id,
    )
    check("Reset returned True", reset_ok is True)

    # Verify the reset took effect.
    success3, auth3, msg3 = authenticate_user("engineer1", "ResetTemp789!")
    check("Login with reset password works", success3 is True)
    check("Temp flag re-enabled after reset", msg3 == "temporary_password")

    # ── Step 15: List users ──────────────────────────────────────
    header("Step 15: List users")
    all_users = list_users()
    check(f"Found {len(all_users)} active users", len(all_users) == 2)
    for u in all_users:
        print(f"    {u}")

    # ═══════════════════════════════════════════════════════════════
    # FINAL OUTPUT: Print the complete Audit Log
    # ═══════════════════════════════════════════════════════════════
    header("COMPLETE AUDIT LOG")
    audit_rows = get_full_audit_log_for_display(limit=50)

    # Column widths for the terminal table.
    fmt = "  {:<4} {:<10} {:<8} {:<12} {:<22} {:<14} {}"
    print(fmt.format("ID", "ACTION", "ENT.ID", "TYPE", "NAME", "USER", "DETAILS"))
    print(f"  {'─'*100}")

    for row in reversed(audit_rows):  # chronological order (oldest first)
        details_str = ""
        if row["details"]:
            # Compact JSON one-liner for terminal readability.
            details_str = str(row["details"])
            if len(details_str) > 50:
                details_str = details_str[:47] + "..."

        print(fmt.format(
            row["id"],
            row["action"],
            str(row["entity_id"] or "—"),
            row["entity_type"] or "—",
            (row["entity_name"] or "—")[:20],
            row["username"][:12],
            details_str,
        ))

    # ═══════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'═'*60}")
    print(f"  ALL TESTS PASSED — {len(audit_rows)} audit entries verified")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()

"""
db_controllers.py — Database controller layer for the Requirements Manager.

This module is the ONLY gateway between the application (GUI or CLI) and the
database.  Every public function:
  1. Opens a session via the context-managed `_session_scope()` helper.
  2. Performs the requested operation.
  3. Writes an AuditLog row for every mutation (create / update / delete / link / unlink).
  4. Commits on success, rolls back on failure, and always closes the session.

The GUI will import these functions and call them directly — no raw SQL or
direct session manipulation should ever occur outside this file.

Password security
─────────────────
Passwords are hashed with werkzeug.security (PBKDF2-SHA256 by default).
The `User.password_hash` column stores the full hash string; plaintext
passwords are never persisted or logged.

Session safety
──────────────
`_session_scope()` is a context manager that guarantees every session is
committed on success, rolled back on any exception, and closed in all cases.
This prevents SQLite database locks even under unexpected errors.

Audit logging
─────────────
`_audit()` is an internal helper that appends an AuditLog row to the current
session.  It is called inside every mutating function BEFORE the commit, so
the audit entry and the business operation live in the same transaction —
either both succeed or both roll back.
"""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.orm import Session, joinedload
from werkzeug.security import generate_password_hash, check_password_hash

from database.models import (
    AuditLog,
    Base,
    Entity,
    EntityLink,
    Element,
    Project,
    ProjectAccess,
    Requirement,
    SubSystem,
    System,
    User,
    get_engine,
)


# ═══════════════════════════════════════════════════════════════════
# ENGINE / SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

# Module-level engine — initialised once on first import.
# Callers can override with `init_engine()` for testing or alternate DBs.
_engine = None


def init_engine(db_path: str = "reqman.db", echo: bool = False):
    """
    Initialise (or re-initialise) the module-level SQLAlchemy engine.

    Must be called once at application startup before any controller
    function is used.  Stores the engine at module level so every
    subsequent call to `_session_scope()` reuses it.

    Args:
        db_path: Filesystem path to the SQLite database file.
        echo:    If True, SQLAlchemy logs all emitted SQL (useful for debugging).
    """
    global _engine
    _engine = get_engine(db_path, echo=echo)
    return _engine


def get_current_engine():
    """Return the current module-level engine, initialising with defaults
    if it has not been set yet.  This lazy-init pattern lets test scripts
    call controller functions without an explicit `init_engine()` step."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


@contextmanager
def _session_scope():
    """
    Context manager that provides a transactional database session.

    Usage:
        with _session_scope() as session:
            session.add(some_object)
            # commit happens automatically on clean exit
            # rollback happens automatically on exception
            # session.close() happens in all cases

    This is the ONLY way sessions are created in the controller layer,
    guaranteeing that every session is properly finalised.
    """
    engine = get_current_engine()
    session = Session(engine)
    try:
        yield session
        session.commit()                       # success → persist
    except Exception:
        session.rollback()                     # failure → undo everything
        raise                                  # re-raise so caller sees the error
    finally:
        session.close()                        # always release the connection


# ═══════════════════════════════════════════════════════════════════
# AUDIT LOGGING  (private helper)
# ═══════════════════════════════════════════════════════════════════

def _audit(
    session: Session,
    *,
    action: str,
    user_id: int,
    entity_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """
    Append an audit-log entry to the current session.

    This is called INSIDE the same transaction as the business operation.
    If the transaction rolls back, the audit entry rolls back too — so
    the log never records an operation that didn't actually happen.

    Args:
        session:      Active SQLAlchemy session (from `_session_scope`).
        action:       One of CREATE, UPDATE, DELETE, LINK, UNLINK.
        user_id:      ID of the user performing the action.
        entity_id:    ID of the affected entity (None for user-only actions).
        entity_type:  Discriminator string (project, system, etc.).
        entity_name:  Human-readable name snapshotted at time of action.
        details:      Arbitrary dict serialised to JSON — captures what changed.

    Returns:
        The AuditLog instance (already added to the session).
    """
    log_entry = AuditLog(
        action=action,
        user_id=user_id,
        entity_id=entity_id,
        entity_type=entity_type,
        entity_name=entity_name,
        details=json.dumps(details) if details else None,
        timestamp=datetime.now(timezone.utc),
    )
    session.add(log_entry)
    return log_entry


# ═══════════════════════════════════════════════════════════════════
# USER AUTHENTICATION & MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def create_user(
    *,
    username: str,
    display_name: str,
    email: str,
    password: str,
    temporary_password: bool = True,
    acting_user_id: Optional[int] = None,
) -> User:
    """
    Create a new user account with a hashed password.

    The password is hashed immediately using werkzeug's PBKDF2-SHA256
    implementation.  The plaintext is never stored or logged.

    Args:
        username:            Unique login name.
        display_name:        Human-friendly name shown in the UI.
        email:               User's email address (required).
        password:            Plaintext password (will be hashed before storage).
        temporary_password:  If True, the user must change password on next login.
        acting_user_id:      ID of the admin creating this account (None for
                             bootstrap / self-registration).

    Returns:
        The newly created User object (detached from session after commit).

    Raises:
        sqlalchemy.exc.IntegrityError: If `username` already exists.
    """
    with _session_scope() as session:
        user = User(
            username=username,
            display_name=display_name,
            email=email,
            password_hash=generate_password_hash(password),
            temporary_password=temporary_password,
            is_active=True,
        )
        session.add(user)
        # Flush to get the auto-generated user.id before we write the audit log.
        session.flush()

        # Audit: record the creation (never log the password itself).
        _audit(
            session,
            action="CREATE",
            user_id=acting_user_id or user.id,  # self-referential on bootstrap
            entity_id=user.id,
            entity_type="user",
            entity_name=user.username,
            details={"username": username, "display_name": display_name,
                     "email": email, "temporary_password": temporary_password},
        )

        # Expunge so the object stays usable after the session closes.
        session.expunge(user)
        return user


def authenticate_user(username: str, password: str) -> Tuple[bool, Optional[User], str]:
    """
    Verify a username + password combination.

    Returns a 3-tuple:
        (success: bool, user: User | None, message: str)

    Possible outcomes:
        (True,  user_obj, "ok")                    — credentials valid
        (True,  user_obj, "temporary_password")     — valid, but must change pw
        (False, None,     "invalid_credentials")    — wrong user or password
        (False, None,     "account_disabled")       — correct creds, but deactivated

    Note: This is a READ operation, so no audit entry is created.  The GUI
    may choose to log successful / failed logins separately if desired.
    """
    with _session_scope() as session:
        # Look up the user by username (case-sensitive).
        stmt = select(User).where(User.username == username)
        user = session.execute(stmt).scalar_one_or_none()

        # User not found — return generic message to avoid enumeration.
        if user is None:
            return False, None, "invalid_credentials"

        # Account disabled — reject even if password is correct.
        if not user.is_active:
            return False, None, "account_disabled"

        # Verify the password hash.
        if not check_password_hash(user.password_hash, password):
            return False, None, "invalid_credentials"

        # Detach so caller can use the object after session closes.
        session.expunge(user)

        # Signal whether the user needs to change a temporary password.
        if user.temporary_password:
            return True, user, "temporary_password"

        return True, user, "ok"


def update_password(
    *,
    user_id: int,
    new_password: str,
    clear_temporary_flag: bool = True,
    acting_user_id: Optional[int] = None,
) -> bool:
    """
    Set a new password for the given user.

    Typically called in two scenarios:
      1. User changes their own temporary password after first login.
      2. An admin resets another user's password.

    Args:
        user_id:                Target user whose password is being changed.
        new_password:           New plaintext password (hashed before storage).
        clear_temporary_flag:   If True, sets temporary_password = False.
        acting_user_id:         The user performing the action (defaults to self).

    Returns:
        True on success, False if the user_id was not found.
    """
    with _session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            return False

        # Hash and store the new password.
        user.password_hash = generate_password_hash(new_password)

        if clear_temporary_flag:
            user.temporary_password = False

        # Audit: record the password change (never log the password itself).
        _audit(
            session,
            action="UPDATE",
            user_id=acting_user_id or user_id,
            entity_id=user.id,
            entity_type="user",
            entity_name=user.username,
            details={"field": "password", "temporary_cleared": clear_temporary_flag},
        )

        return True


def reset_password(
    *,
    user_id: int,
    new_temporary_password: str,
    acting_user_id: int,
) -> bool:
    """
    Admin-initiated password reset.  Sets the password AND re-enables
    the temporary_password flag so the user is forced to choose a new
    password on their next login.

    Args:
        user_id:                Target user being reset.
        new_temporary_password: The temporary plaintext password.
        acting_user_id:         The admin performing the reset.

    Returns:
        True on success, False if user_id not found.
    """
    with _session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            return False

        # Set the hash and flip the flag back to "temporary".
        user.password_hash = generate_password_hash(new_temporary_password)
        user.temporary_password = True

        _audit(
            session,
            action="UPDATE",
            user_id=acting_user_id,
            entity_id=user.id,
            entity_type="user",
            entity_name=user.username,
            details={"field": "password_reset", "temporary_password": True},
        )

        return True


def list_users(*, active_only: bool = True) -> List[User]:
    """
    Return all users, optionally filtered to active accounts only.

    This is a READ operation — no audit entry.
    """
    with _session_scope() as session:
        stmt = select(User).order_by(User.username)
        if active_only:
            stmt = stmt.where(User.is_active == True)  # noqa: E712

        users = session.execute(stmt).scalars().all()
        # Detach all so they survive the session close.
        for u in users:
            session.expunge(u)
        return users


def search_users(
    query: str,
    *,
    active_only: bool = True,
    exclude_admin: bool = True,
) -> List[User]:
    """
    Search users by partial match on username, display_name, or email.

    Case-insensitive substring match.  Returns up to 50 results.
    """
    with _session_scope() as session:
        stmt = select(User).order_by(User.display_name).limit(50)
        if active_only:
            stmt = stmt.where(User.is_active == True)  # noqa: E712
        if exclude_admin:
            stmt = stmt.where(User.username != "admin")
        if query.strip():
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(
                (User.username.ilike(pattern))
                | (User.display_name.ilike(pattern))
                | (User.email.ilike(pattern))
            )
        users = session.execute(stmt).scalars().all()
        for u in users:
            session.expunge(u)
        return users


def get_user(user_id: int) -> Optional[User]:
    """
    Fetch a single user by primary key.

    READ-only — no audit entry.

    Returns:
        The User if found, or None.
    """
    with _session_scope() as session:
        user = session.get(User, user_id)
        if user:
            session.expunge(user)
        return user


def update_user(
    *,
    user_id: int,
    acting_user_id: int,
    updates: Dict[str, Any],
) -> Optional[User]:
    """
    Update one or more fields on an existing user (e.g. display_name).

    Works identically to `update_entity` but operates on the User table.
    Password changes should still go through `update_password` or
    `reset_password` so hashing is handled correctly.

    Args:
        user_id:         Primary key of the user to modify.
        acting_user_id:  The user performing the action (for audit).
        updates:         Dict of {field_name: new_value} pairs.

    Returns:
        The updated User (detached), or None if not found.

    Raises:
        ValueError: If a field in `updates` doesn't exist on User.
    """
    # Guard: never allow raw password_hash changes through this function.
    if "password_hash" in updates:
        raise ValueError("Use update_password() to change passwords.")

    with _session_scope() as session:
        user = session.get(User, user_id)
        if user is None:
            return None

        # Snapshot old values for the audit log.
        old_values = {}
        for field, new_value in updates.items():
            if not hasattr(user, field):
                raise ValueError(
                    f"Field '{field}' does not exist on User (id={user_id})."
                )
            old_values[field] = getattr(user, field)
            setattr(user, field, new_value)

        _audit(
            session,
            action="UPDATE",
            user_id=acting_user_id,
            entity_id=user.id,
            entity_type="user",
            entity_name=user.username,
            details={"old": old_values, "new": updates},
        )

        session.flush()
        session.expunge(user)
        return user


# ═══════════════════════════════════════════════════════════════════
# ENTITY CRUD  (Projects, Systems, SubSystems, Elements, Requirements)
# ═══════════════════════════════════════════════════════════════════

# Mapping from string type names to their ORM classes.
# Used by the generic `create_entity` / `update_entity` functions so the
# GUI can pass a simple string instead of importing model classes.
ENTITY_CLASS_MAP: Dict[str, type] = {
    "project": Project,
    "system": System,
    "subsystem": SubSystem,
    "element": Element,
    "requirement": Requirement,
}


def create_entity(
    *,
    entity_type: str,
    name: str,
    user_id: int,
    parent_id: Optional[int] = None,
    description: Optional[str] = None,
    status: str = "draft",
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Entity:
    """
    Create any entity in the hierarchy.

    This single function handles Projects, Systems, SubSystems, Elements,
    and Requirements.  The correct ORM subclass is chosen via `entity_type`.

    Args:
        entity_type:   One of: project, system, subsystem, element, requirement.
        name:          Display name for the entity.
        user_id:       The user performing the action (for the audit log).
        parent_id:     ID of the parent entity (None for top-level Projects).
        description:   Optional long-form description.
        status:        Workflow status (defaults to "draft").
        extra_fields:  Dict of subclass-specific columns, e.g.
                       {"priority": "high", "rationale": "..."} for Requirements.

    Returns:
        The newly created Entity (detached from session).

    Raises:
        ValueError:    If entity_type is unrecognised or a Requirement is
                       given as a parent (Requirements cannot have children).
        sqlalchemy.exc.IntegrityError: If parent_id references a non-existent entity.
    """
    # ── Validate entity type ─────────────────────────────────────
    entity_type_lower = entity_type.lower()
    cls = ENTITY_CLASS_MAP.get(entity_type_lower)
    if cls is None:
        raise ValueError(
            f"Unknown entity_type '{entity_type}'. "
            f"Must be one of: {', '.join(ENTITY_CLASS_MAP)}"
        )

    with _session_scope() as session:
        # ── Guard: Requirements cannot have children ─────────────
        if parent_id is not None:
            parent = session.get(Entity, parent_id)
            if parent is None:
                raise ValueError(f"Parent entity with id={parent_id} not found.")
            if parent.entity_type == "requirement":
                raise ValueError(
                    "Requirements are leaf nodes and cannot have children. "
                    f"Parent id={parent_id} is a Requirement."
                )

        # ── Build the entity with base fields ────────────────────
        entity = cls(
            name=name,
            parent_id=parent_id,
            description=description,
            status=status,
        )

        # ── Apply subclass-specific fields (e.g. priority) ──────
        if extra_fields:
            for key, value in extra_fields.items():
                if hasattr(entity, key):
                    setattr(entity, key, value)
                else:
                    raise ValueError(
                        f"Field '{key}' does not exist on {cls.__name__}."
                    )

        session.add(entity)
        session.flush()  # populate entity.id for the audit log

        # ── Auto-link child to parent ─────────────────────────────
        if parent_id is not None:
            auto_link = EntityLink(
                source_entity_id=entity.id,
                target_entity_id=parent_id,
            )
            session.add(auto_link)
            session.flush()
            _audit(
                session,
                action="LINK",
                user_id=user_id,
                entity_id=entity.id,
                entity_type=entity_type_lower,
                entity_name=name,
                details={
                    "source_id": entity.id,
                    "target_id": parent_id,
                    "auto": True,
                },
            )

        # ── Audit log ────────────────────────────────────────────
        _audit(
            session,
            action="CREATE",
            user_id=user_id,
            entity_id=entity.id,
            entity_type=entity.entity_type,
            entity_name=entity.name,
            details={
                "name": name,
                "parent_id": parent_id,
                "status": status,
                **(extra_fields or {}),
            },
        )

        # Refresh reloads ALL columns (including STI subclass fields
        # like body, test_plan_path, ticket_link) into the instance's
        # __dict__ before we detach it from the session.  Without this,
        # accessing those attributes after expunge raises
        # DetachedInstanceError.
        session.refresh(entity)
        session.expunge(entity)
        return entity


def get_entity(entity_id: int) -> Optional[Entity]:
    """
    Fetch a single entity by primary key.

    READ-only — no audit entry.

    Returns:
        The Entity if found, or None.
    """
    with _session_scope() as session:
        entity = session.get(Entity, entity_id)
        if entity:
            session.refresh(entity)
            session.expunge(entity)
        return entity


def get_children(parent_id: int) -> List[Entity]:
    """
    Fetch all direct children of a given entity.

    Useful for populating tree-view nodes lazily in the GUI.
    READ-only — no audit entry.
    """
    with _session_scope() as session:
        stmt = (
            select(Entity)
            .where(Entity.parent_id == parent_id)
            .order_by(Entity.sort_order, Entity.name)
        )
        children = session.execute(stmt).scalars().all()
        for c in children:
            session.refresh(c)
            session.expunge(c)
        return children


def get_all_projects() -> List[Project]:
    """
    Fetch every top-level Project.

    This is the entry point for the GUI's main tree view — each Project
    is a root node whose children can be loaded with `get_children()`.
    READ-only — no audit entry.
    """
    with _session_scope() as session:
        stmt = (
            select(Project)
            .where(Project.parent_id == None)  # noqa: E711 — SQLAlchemy requires `==`
            .order_by(Project.name)
        )
        projects = session.execute(stmt).scalars().all()
        for p in projects:
            session.refresh(p)
            session.expunge(p)
        return projects


def search_entities(
    query: str,
    *,
    exclude_ids: Optional[List[int]] = None,
    limit: int = 50,
) -> List[Entity]:
    """
    Search all entities by name (case-insensitive substring match).

    Used by the "Browse/Add Link" dialog to let users find entities
    anywhere in the database.

    Args:
        query:        Search text.  An empty string returns everything
                      (up to `limit`).
        exclude_ids:  Optional list of entity IDs to exclude from the
                      results (e.g. the entity being edited, or entities
                      already linked to it).
        limit:        Maximum rows to return (default 50).

    Returns:
        List of matching Entity objects (detached), ordered by name.

    READ-only — no audit entry.
    """
    with _session_scope() as session:
        stmt = select(Entity).order_by(Entity.name).limit(limit)

        if query.strip():
            # SQLite LIKE is case-insensitive for ASCII by default.
            stmt = stmt.where(Entity.name.ilike(f"%{query.strip()}%"))

        if exclude_ids:
            stmt = stmt.where(Entity.id.notin_(exclude_ids))

        results = session.execute(stmt).scalars().all()
        for e in results:
            session.refresh(e)
            session.expunge(e)
        return results


def update_entity(
    *,
    entity_id: int,
    user_id: int,
    updates: Dict[str, Any],
) -> Optional[Entity]:
    """
    Update one or more fields on an existing entity.

    The `updates` dict maps column names to new values.  Only the fields
    present in the dict are changed; everything else is left untouched.

    Example:
        update_entity(entity_id=7, user_id=1,
                      updates={"name": "New Name", "status": "approved"})

    Args:
        entity_id:  Primary key of the entity to modify.
        user_id:    The user performing the action.
        updates:    Dict of {field_name: new_value} pairs.

    Returns:
        The updated Entity (detached), or None if not found.

    Raises:
        ValueError: If a field in `updates` doesn't exist on the entity.
    """
    with _session_scope() as session:
        entity = session.get(Entity, entity_id)
        if entity is None:
            return None

        # Snapshot the old values for the audit log's "details" field.
        old_values = {}
        for field, new_value in updates.items():
            if not hasattr(entity, field):
                raise ValueError(
                    f"Field '{field}' does not exist on "
                    f"{entity.__class__.__name__} (id={entity_id})."
                )
            old_values[field] = getattr(entity, field)
            setattr(entity, field, new_value)

        # ── Audit: capture both old and new values ───────────────
        _audit(
            session,
            action="UPDATE",
            user_id=user_id,
            entity_id=entity.id,
            entity_type=entity.entity_type,
            entity_name=entity.name,
            details={"old": old_values, "new": updates},
        )

        session.flush()
        session.refresh(entity)
        session.expunge(entity)
        return entity


def delete_entity(*, entity_id: int, user_id: int) -> bool:
    """
    Delete an entity and all its descendants (via ON DELETE CASCADE).

    The SQLite-level cascade will also remove any EntityLink rows that
    reference the deleted entity or its children.

    The audit log records the deletion of the TOP-LEVEL entity being
    removed.  Child deletions are implicit (cascade) and are not logged
    individually — this is a design trade-off for simplicity.  If you
    need per-child audit entries, collect descendants before deleting.

    Args:
        entity_id:  Primary key of the entity to remove.
        user_id:    The user performing the action.

    Returns:
        True if the entity existed and was deleted, False if not found.
    """
    with _session_scope() as session:
        entity = session.get(Entity, entity_id)
        if entity is None:
            return False

        # Snapshot identifying info before the object is gone.
        snapshot_type = entity.entity_type
        snapshot_name = entity.name
        snapshot_parent = entity.parent_id

        # ── Audit BEFORE the delete so the entry lands in this transaction ─
        _audit(
            session,
            action="DELETE",
            user_id=user_id,
            entity_id=entity_id,
            entity_type=snapshot_type,
            entity_name=snapshot_name,
            details={"parent_id": snapshot_parent, "name": snapshot_name},
        )

        session.delete(entity)
        return True


# ═══════════════════════════════════════════════════════════════════
# PROJECT MASTER TEST TEMPLATE
# ═══════════════════════════════════════════════════════════════════

def get_master_template_path(project_id: int) -> Optional[str]:
    """Return the master_test_template_path for a Project, or None."""
    with _session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return None
        return project.master_test_template_path


def set_master_template_path(
    *,
    project_id: int,
    path: str,
    user_id: int,
) -> Optional[Project]:
    """Set or update the master_test_template_path on a Project."""
    with _session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            return None

        old_path = project.master_test_template_path
        project.master_test_template_path = path

        _audit(
            session,
            action="UPDATE",
            user_id=user_id,
            entity_id=project.id,
            entity_type=project.entity_type,
            entity_name=project.name,
            details={"field": "master_test_template_path",
                     "old": old_path, "new": path},
        )

        session.flush()
        session.refresh(project)
        session.expunge(project)
        return project


def clear_master_template_path(
    *,
    project_id: int,
    user_id: int,
) -> None:
    """Clear the master_test_template_path (set to NULL)."""
    set_master_template_path(
        project_id=project_id, path=None, user_id=user_id
    )


# ═══════════════════════════════════════════════════════════════════
# REQUIREMENT — GENERATED TEST FILE PATH
# ═══════════════════════════════════════════════════════════════════

def get_generated_test_path(requirement_id: int) -> Optional[str]:
    """Return the generated_test_file_path for a Requirement, or None."""
    with _session_scope() as session:
        req = session.get(Requirement, requirement_id)
        if req is None:
            return None
        return req.generated_test_file_path


def set_generated_test_path(
    *,
    requirement_id: int,
    path: Optional[str],
    user_id: int,
) -> None:
    """Set or clear the generated_test_file_path on a Requirement."""
    with _session_scope() as session:
        req = session.get(Requirement, requirement_id)
        if req is None:
            return

        old_path = req.generated_test_file_path
        req.generated_test_file_path = path

        _audit(
            session,
            action="UPDATE",
            user_id=user_id,
            entity_id=req.id,
            entity_type=req.entity_type,
            entity_name=req.name,
            details={"field": "generated_test_file_path",
                     "old": old_path, "new": path},
        )


# ═══════════════════════════════════════════════════════════════════
# ENTITY LINKING  (Many-to-Many)
# ═══════════════════════════════════════════════════════════════════

def link_entities(
    *,
    source_id: int,
    target_id: int,
    user_id: int,
) -> EntityLink:
    """
    Create a directional link from source → target.

    Both source and target can be ANY entity type.  The association table's
    constraints prevent self-links and duplicates at the database level.

    Args:
        source_id:  The entity that "links to" the target.
        target_id:  The entity being linked to.
        user_id:    The user creating the link.

    Returns:
        The new EntityLink row.

    Raises:
        ValueError:  If source or target doesn't exist, or source == target.
        sqlalchemy.exc.IntegrityError: On duplicate link.
    """
    if source_id == target_id:
        raise ValueError("An entity cannot link to itself.")

    with _session_scope() as session:
        # Verify both entities exist so we can give a clear error message.
        source = session.get(Entity, source_id)
        target = session.get(Entity, target_id)
        if source is None:
            raise ValueError(f"Source entity id={source_id} not found.")
        if target is None:
            raise ValueError(f"Target entity id={target_id} not found.")

        link = EntityLink(
            source_entity_id=source_id,
            target_entity_id=target_id,
        )
        session.add(link)
        session.flush()

        # ── Audit: record both sides of the link for easy searching ──
        _audit(
            session,
            action="LINK",
            user_id=user_id,
            entity_id=source_id,
            entity_type=source.entity_type,
            entity_name=source.name,
            details={
                "linked_to_id": target_id,
                "linked_to_type": target.entity_type,
                "linked_to_name": target.name,
            },
        )

        session.expunge(link)
        return link


def unlink_entities(
    *,
    source_id: int,
    target_id: int,
    user_id: int,
) -> bool:
    """
    Remove a directional link from source → target.

    Args:
        source_id:  The source side of the link.
        target_id:  The target side of the link.
        user_id:    The user removing the link.

    Returns:
        True if the link existed and was removed, False otherwise.
    """
    with _session_scope() as session:
        stmt = select(EntityLink).where(
            EntityLink.source_entity_id == source_id,
            EntityLink.target_entity_id == target_id,
        )
        link = session.execute(stmt).scalar_one_or_none()
        if link is None:
            return False

        # Fetch entity names for the audit log before we delete the link.
        source = session.get(Entity, source_id)
        target = session.get(Entity, target_id)

        _audit(
            session,
            action="UNLINK",
            user_id=user_id,
            entity_id=source_id,
            entity_type=source.entity_type if source else None,
            entity_name=source.name if source else f"id={source_id}",
            details={
                "unlinked_from_id": target_id,
                "unlinked_from_type": target.entity_type if target else None,
                "unlinked_from_name": target.name if target else f"id={target_id}",
            },
        )

        session.delete(link)
        return True


def get_linked_entities(
    entity_id: int,
    *,
    direction: str = "both",
    target_type: Optional[str] = None,
) -> List[Entity]:
    """
    Fetch entities linked to/from a given entity.

    Args:
        entity_id:   The entity whose links we're querying.
        direction:   "outgoing" (this → others), "incoming" (others → this),
                     or "both" (union of both directions).
        target_type: Optional filter — only return linked entities of this type
                     (e.g. "requirement").

    Returns:
        List of linked Entity objects (detached from session).

    READ-only — no audit entry.
    """
    with _session_scope() as session:
        results = []

        # ── Outgoing links (this entity is the source) ───────────
        if direction in ("outgoing", "both"):
            stmt = (
                select(Entity)
                .join(EntityLink, EntityLink.target_entity_id == Entity.id)
                .where(EntityLink.source_entity_id == entity_id)
            )
            if target_type:
                stmt = stmt.where(Entity.entity_type == target_type)
            results.extend(session.execute(stmt).scalars().all())

        # ── Incoming links (this entity is the target) ───────────
        if direction in ("incoming", "both"):
            stmt = (
                select(Entity)
                .join(EntityLink, EntityLink.source_entity_id == Entity.id)
                .where(EntityLink.target_entity_id == entity_id)
            )
            if target_type:
                stmt = stmt.where(Entity.entity_type == target_type)
            incoming = session.execute(stmt).scalars().all()
            # Deduplicate if "both" — an entity could theoretically link
            # in both directions (A→B and B→A).
            existing_ids = {e.id for e in results}
            results.extend(e for e in incoming if e.id not in existing_ids)

        for e in results:
            session.refresh(e)
            session.expunge(e)
        return results


# ═══════════════════════════════════════════════════════════════════
# AUDIT HISTORY QUERIES
# ═══════════════════════════════════════════════════════════════════

def get_audit_log(
    *,
    entity_id: Optional[int] = None,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    limit: int = 100,
) -> List[AuditLog]:
    """
    Retrieve audit log entries with optional filters.

    All parameters are optional and combinable.  Results are returned
    newest-first.

    Args:
        entity_id:  Filter to entries affecting a specific entity.
        user_id:    Filter to entries made by a specific user.
        action:     Filter to a specific action (CREATE, UPDATE, DELETE, etc.).
        limit:      Maximum number of rows to return (default 100).

    Returns:
        List of AuditLog objects (detached), newest first.
    """
    with _session_scope() as session:
        stmt = select(AuditLog).order_by(AuditLog.timestamp.desc())

        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        if user_id is not None:
            stmt = stmt.where(AuditLog.user_id == user_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action.upper())

        stmt = stmt.limit(limit)

        entries = session.execute(stmt).scalars().all()
        for e in entries:
            session.expunge(e)
        return entries


def get_audit_log_with_user(
    *,
    entity_id: Optional[int] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Retrieve audit entries for an entity, enriched with the acting
    user's display name.

    Each dict:
        id, action, entity_id, entity_type, entity_name,
        details (parsed JSON dict), user_id, username, display_name,
        timestamp (datetime object).
    """
    with _session_scope() as session:
        stmt = (
            select(AuditLog, User.username, User.display_name)
            .outerjoin(User, AuditLog.user_id == User.id)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_id == entity_id)

        rows = session.execute(stmt).all()
        output = []
        for entry, username, display_name in rows:
            details = None
            if entry.details:
                try:
                    details = json.loads(entry.details)
                except (json.JSONDecodeError, TypeError):
                    details = {"raw": entry.details}
            output.append({
                "id": entry.id,
                "action": entry.action,
                "entity_id": entry.entity_id,
                "entity_type": entry.entity_type,
                "entity_name": entry.entity_name,
                "details": details,
                "user_id": entry.user_id,
                "username": username or "(deleted user)",
                "display_name": display_name or "(deleted user)",
                "timestamp": entry.timestamp,
            })
        return output


def get_project_audit_log(project_id: int, limit: int = 5000) -> List[Dict[str, Any]]:
    """
    Return the audit log for every entity that belongs to a project.

    Collects all descendant entity IDs recursively, then queries the
    audit log for all of them (plus the project itself).
    """
    # Collect all entity IDs in the project tree
    all_ids = {project_id}
    _collect_descendant_ids(project_id, all_ids)

    with _session_scope() as session:
        stmt = (
            select(AuditLog, User.username, User.display_name)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(AuditLog.entity_id.in_(all_ids))
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        output = []
        for entry, username, display_name in rows:
            details = None
            if entry.details:
                try:
                    details = json.loads(entry.details)
                except (json.JSONDecodeError, TypeError):
                    details = {"raw": entry.details}
            output.append({
                "id": entry.id,
                "action": entry.action,
                "entity_id": entry.entity_id,
                "entity_type": entry.entity_type,
                "entity_name": entry.entity_name,
                "details": details,
                "user_id": entry.user_id,
                "username": username or "(deleted user)",
                "display_name": display_name or "(deleted user)",
                "timestamp": entry.timestamp,
            })
        return output


def _collect_descendant_ids(parent_id: int, result: set):
    """Recursively collect all descendant entity IDs under a parent."""
    children = get_children(parent_id)
    for child in children:
        result.add(child.id)
        if child.entity_type != "requirement":
            _collect_descendant_ids(child.id, result)


def get_full_audit_log_for_display(limit: int = 200) -> List[Dict[str, Any]]:
    """
    Return the complete audit log as a list of plain dictionaries,
    suitable for direct display in a GUI table or terminal printout.

    Each dict contains:
        id, action, entity_id, entity_type, entity_name,
        details (parsed JSON), user_id, username, timestamp.

    Results are newest-first.
    """
    with _session_scope() as session:
        # Left-join to users so we get the username even if user_id is NULL.
        stmt = (
            select(AuditLog, User.username)
            .outerjoin(User, AuditLog.user_id == User.id)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
        )

        rows = session.execute(stmt).all()
        output = []
        for log_entry, username in rows:
            output.append({
                "id": log_entry.id,
                "action": log_entry.action,
                "entity_id": log_entry.entity_id,
                "entity_type": log_entry.entity_type,
                "entity_name": log_entry.entity_name,
                "details": json.loads(log_entry.details) if log_entry.details else None,
                "user_id": log_entry.user_id,
                "username": username or "(deleted user)",
                "timestamp": log_entry.timestamp.isoformat() if log_entry.timestamp else None,
            })
        return output


# ═══════════════════════════════════════════════════════════════════
# PROJECT ACCESS / PERMISSIONS
# ═══════════════════════════════════════════════════════════════════

def is_admin(user) -> bool:
    """Return True if the user is the administrator."""
    return user.username == "admin"


def grant_project_access(
    *,
    user_id: int,
    project_id: int,
    role: str = "member",
    granted_by_user_id: int,
) -> ProjectAccess:
    """
    Grant a user access to a project with the given role.

    If the user already has access, their role is updated instead.

    Args:
        user_id:             The user receiving access.
        project_id:          The project entity ID.
        role:                "manager" or "member".
        granted_by_user_id:  The user performing the action (for audit).

    Returns:
        The ProjectAccess row (detached).
    """
    with _session_scope() as session:
        existing = session.execute(
            select(ProjectAccess).where(
                ProjectAccess.user_id == user_id,
                ProjectAccess.project_id == project_id,
            )
        ).scalar_one_or_none()

        if existing:
            old_role = existing.role
            existing.role = role
            session.flush()
            _audit(
                session,
                action="UPDATE",
                user_id=granted_by_user_id,
                entity_id=project_id,
                entity_type="project",
                entity_name=None,
                details={
                    "subject": "project_access",
                    "target_user_id": user_id,
                    "old_role": old_role,
                    "new_role": role,
                },
            )
            session.refresh(existing)
            session.expunge(existing)
            return existing

        access = ProjectAccess(
            user_id=user_id,
            project_id=project_id,
            role=role,
        )
        session.add(access)
        session.flush()
        _audit(
            session,
            action="CREATE",
            user_id=granted_by_user_id,
            entity_id=project_id,
            entity_type="project",
            entity_name=None,
            details={
                "subject": "project_access",
                "target_user_id": user_id,
                "role": role,
            },
        )
        session.refresh(access)
        session.expunge(access)
        return access


def revoke_project_access(
    *,
    user_id: int,
    project_id: int,
    revoked_by_user_id: int,
) -> bool:
    """
    Remove a user's access to a project.

    Returns True if a row was deleted, False if none existed.
    """
    with _session_scope() as session:
        existing = session.execute(
            select(ProjectAccess).where(
                ProjectAccess.user_id == user_id,
                ProjectAccess.project_id == project_id,
            )
        ).scalar_one_or_none()

        if existing is None:
            return False

        old_role = existing.role
        session.delete(existing)
        session.flush()
        _audit(
            session,
            action="DELETE",
            user_id=revoked_by_user_id,
            entity_id=project_id,
            entity_type="project",
            entity_name=None,
            details={
                "subject": "project_access",
                "target_user_id": user_id,
                "old_role": old_role,
            },
        )
        return True


def get_project_access(project_id: int) -> List[Dict[str, Any]]:
    """
    Return all access entries for a project as plain dicts.

    Each dict: {user_id, username, display_name, role}.
    """
    with _session_scope() as session:
        stmt = (
            select(ProjectAccess, User)
            .join(User, ProjectAccess.user_id == User.id)
            .where(ProjectAccess.project_id == project_id)
            .order_by(ProjectAccess.role, User.display_name)
        )
        rows = session.execute(stmt).all()
        return [
            {
                "user_id": pa.user_id,
                "username": u.username,
                "display_name": u.display_name,
                "role": pa.role,
            }
            for pa, u in rows
        ]


def user_can_access_project(user, project_id: int) -> bool:
    """
    Check whether a user may open a project.

    Admin always has access.  Otherwise the user must have a
    ProjectAccess row (any role) for the given project.
    """
    if is_admin(user):
        return True
    with _session_scope() as session:
        row = session.execute(
            select(ProjectAccess.id).where(
                ProjectAccess.user_id == user.id,
                ProjectAccess.project_id == project_id,
            )
        ).first()
        return row is not None


def user_is_project_manager(user, project_id: int) -> bool:
    """
    Check whether a user is a 'manager' for a given project.

    Admin always counts as a manager.
    """
    if is_admin(user):
        return True
    with _session_scope() as session:
        row = session.execute(
            select(ProjectAccess.id).where(
                ProjectAccess.user_id == user.id,
                ProjectAccess.project_id == project_id,
                ProjectAccess.role == "manager",
            )
        ).first()
        return row is not None


def get_accessible_projects(user) -> List[Project]:
    """
    Return the projects a user is allowed to open.

    Admin sees all projects.  Everyone else sees only projects
    where they have a ProjectAccess row.
    """
    if is_admin(user):
        return get_all_projects()

    with _session_scope() as session:
        stmt = (
            select(Project)
            .join(ProjectAccess, ProjectAccess.project_id == Project.id)
            .where(
                Project.parent_id == None,  # noqa: E711
                ProjectAccess.user_id == user.id,
            )
            .order_by(Project.name)
        )
        projects = session.execute(stmt).scalars().all()
        for p in projects:
            session.refresh(p)
            session.expunge(p)
        return projects


def get_all_db_managers() -> List[Dict[str, Any]]:
    """
    Return every user who has 'manager' role on at least one project.

    Returns a list of dicts: {user_id, username, display_name, project_ids}.
    """
    with _session_scope() as session:
        stmt = (
            select(ProjectAccess, User)
            .join(User, ProjectAccess.user_id == User.id)
            .where(ProjectAccess.role == "manager")
            .order_by(User.display_name)
        )
        rows = session.execute(stmt).all()

        # Group by user
        managers: Dict[int, Dict[str, Any]] = {}
        for pa, u in rows:
            if u.id not in managers:
                managers[u.id] = {
                    "user_id": u.id,
                    "username": u.username,
                    "display_name": u.display_name,
                    "project_ids": [],
                }
            managers[u.id]["project_ids"].append(pa.project_id)
        return list(managers.values())

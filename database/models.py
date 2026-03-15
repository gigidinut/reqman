"""
models.py — SQLAlchemy ORM schema for the Requirements Management application.

Hierarchy:  Project ➜ System ➜ SubSystem ➜ Element ➜ Requirement
            (parent → child via adjacency + entity_type discrimination)

Cross-cutting:
  • EntityLink        – Many-to-Many association between ANY two entities.
  • AuditLog          – Immutable ledger of every CUD operation.
  • User              – Application users with temporary-password support.

Design decisions
────────────────
1.  Single-Table Inheritance (STI) via `entity_type` discriminator.
    Every node in the hierarchy lives in ONE `entities` table.  This makes
    the M2M link table trivially simple — two FK columns into the same table —
    and keeps cross-type queries fast (no UNIONs, no polymorphic joins).

2.  Composite parent–child relationship uses `parent_id` (self-referential FK)
    with SQLite-level ON DELETE CASCADE so removing a System automatically
    removes its SubSystems, Elements, and Requirements.

3.  EntityLink has ON DELETE CASCADE on both FK sides, so deleting an entity
    automatically cleans up every row in the association table that references
    it.  No orphaned links, ever.

4.  AuditLog is append-only; it references `entity_id` but does NOT cascade
    on delete — history is preserved even after the entity is gone.
"""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    CheckConstraint,
    Index,
    event,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    Session,
)


# ──────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# User
# ──────────────────────────────────────────────
class User(Base):
    """Application user.  `temporary_password` flags accounts that must
    change their password on next login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    temporary_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # back-ref to audit entries authored by this user
    audit_entries: Mapped[List["AuditLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


# ──────────────────────────────────────────────
# Entity  (Single-Table Inheritance root)
# ──────────────────────────────────────────────
ENTITY_TYPES = ("project", "system", "subsystem", "element", "requirement")


class Entity(Base):
    """
    Base row for every node in the hierarchy.

    Discriminated by `entity_type`:
        project | system | subsystem | element | requirement

    Self-referential `parent_id` enforces the tree.  Requirements
    CANNOT have children (enforced at the application layer and via
    the subclass).
    """

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)

    # ── Core fields ──────────────────────────
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(60), default="draft", nullable=False)

    # ── Hierarchy ────────────────────────────
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Timestamps ───────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── STI discriminator ────────────────────
    __mapper_args__ = {
        "polymorphic_on": entity_type,
        "polymorphic_identity": "entity",  # base (never used directly)
    }

    # ── Table-level constraints & indexes ────
    __table_args__ = (
        CheckConstraint(
            f"entity_type IN {ENTITY_TYPES!r}",
            name="ck_entity_type_valid",
        ),
        Index("ix_entity_type", "entity_type"),
    )

    # ── Relationships ────────────────────────
    children: Mapped[List["Entity"]] = relationship(
        "Entity",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    parent: Mapped[Optional["Entity"]] = relationship(
        "Entity",
        back_populates="children",
        remote_side=[id],
    )

    # M2M — entities this entity links TO
    linked_to: Mapped[List["Entity"]] = relationship(
        "Entity",
        secondary="entity_links",
        primaryjoin="Entity.id == EntityLink.source_entity_id",
        secondaryjoin="Entity.id == EntityLink.target_entity_id",
        back_populates="linked_from",
        viewonly=False,
    )

    # M2M — entities that link TO this entity
    linked_from: Mapped[List["Entity"]] = relationship(
        "Entity",
        secondary="entity_links",
        primaryjoin="Entity.id == EntityLink.target_entity_id",
        secondaryjoin="Entity.id == EntityLink.source_entity_id",
        back_populates="linked_to",
        viewonly=False,
    )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id} name={self.name!r}>"


# ──────────────────────────────────────────────
# Concrete entity sub-types (STI children)
# ──────────────────────────────────────────────
class Project(Entity):
    """Top-level container.  parent_id should be NULL."""

    __mapper_args__ = {"polymorphic_identity": "project"}

    # Absolute path to the master test-template file chosen by the user.
    master_test_template_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )


class System(Entity):
    __mapper_args__ = {"polymorphic_identity": "system"}


class SubSystem(Entity):
    __mapper_args__ = {"polymorphic_identity": "subsystem"}


class Element(Entity):
    __mapper_args__ = {"polymorphic_identity": "element"}


class Requirement(Entity):
    """Leaf node — application logic must prevent adding children."""

    __mapper_args__ = {"polymorphic_identity": "requirement"}

    # Requirement-specific fields
    req_id: Mapped[Optional[str]] = mapped_column(
        String(60), nullable=True
    )
    priority: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True, default="medium"
    )
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    test_plan_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ticket_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ai_score: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


# ──────────────────────────────────────────────
# EntityLink  (Many-to-Many association table)
# ──────────────────────────────────────────────
class EntityLink(Base):
    """
    Association table for the "Linked to" Many-to-Many relationship.

    Both FKs cascade on delete so removing either side automatically
    cleans up the link row.

    A UniqueConstraint prevents duplicate links.  The CheckConstraint
    prevents an entity from linking to itself.

    Because this is a full ORM model (not just a `Table` object), you
    can query it directly:

        session.query(EntityLink).filter_by(source_entity_id=42).all()
    """

    __tablename__ = "entity_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_entity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            name="uq_entity_link_pair",
        ),
        CheckConstraint(
            "source_entity_id != target_entity_id",
            name="ck_no_self_link",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<EntityLink {self.source_entity_id} → {self.target_entity_id}>"
        )


# ──────────────────────────────────────────────
# AuditLog
# ──────────────────────────────────────────────
AUDIT_ACTIONS = ("CREATE", "UPDATE", "DELETE", "LINK", "UNLINK")


class AuditLog(Base):
    """
    Append-only audit trail.  Every CUD operation on any entity or link
    is recorded here.

    `entity_id` is intentionally NOT a FK with CASCADE — we want to keep
    the log even after the entity is deleted.  The id is stored as a
    plain integer for historical reference.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    action: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    entity_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # Snapshot of what changed — stored as a JSON-formatted string.
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_entity", "entity_id"),
        Index("ix_audit_timestamp", "timestamp"),
        Index("ix_audit_action", "action"),
    )

    # relationship back to user (nullable — user may be deleted)
    user: Mapped[Optional["User"]] = relationship(back_populates="audit_entries")

    def __repr__(self) -> str:
        return (
            f"<AuditLog {self.action} entity={self.entity_id} "
            f"at {self.timestamp}>"
        )


# ──────────────────────────────────────────────
# SQLite PRAGMA helper  (enables FK enforcement)
# ──────────────────────────────────────────────
def enable_sqlite_fk(dbapi_conn, connection_record):
    """SQLite does not enforce FKs by default.  This listener turns them on."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def get_engine(db_path: str = "reqman.db", echo: bool = False):
    """Create a SQLite engine with FK enforcement enabled."""
    engine = create_engine(f"sqlite:///{db_path}", echo=echo)
    event.listen(engine, "connect", enable_sqlite_fk)
    return engine

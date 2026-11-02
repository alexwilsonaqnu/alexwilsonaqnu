"""seed taxonomy v1 baseline

The v1 snapshot is the baseline, not a change, so it is not written to taxonomy_changes. Every later
change is logged there (agents: propose only; humans: accept/reject).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from solbrief.taxonomy import load_snapshot

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    note, classes = load_snapshot("v1")
    conn = op.get_bind()
    version_id = conn.execute(
        sa.text("INSERT INTO taxonomy_versions (version, note) VALUES ('v1', :note) RETURNING id"),
        {"note": note},
    ).scalar_one()
    for c in classes:  # parents precede children in the snapshot
        conn.execute(
            sa.text(
                "INSERT INTO problem_classes (id, label, parent_id, status, introduced_in_version) "
                "VALUES (:id, :label, :parent, 'active', :v)"
            ),
            {"id": c.id, "label": c.label, "parent": c.parent_id, "v": version_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM taxonomy_changes"))
    conn.execute(sa.text("DELETE FROM problem_classes WHERE parent_id IS NOT NULL"))
    conn.execute(sa.text("DELETE FROM problem_classes"))
    conn.execute(sa.text("DELETE FROM taxonomy_versions WHERE version = 'v1'"))

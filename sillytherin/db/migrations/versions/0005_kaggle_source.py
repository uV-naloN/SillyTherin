"""Allow the Kaggle dataset source without changing existing pool links."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SOURCES_BEFORE = "source IN ('huggingface', 'zenodo', 'link')"
SOURCES_AFTER = "source IN ('huggingface', 'zenodo', 'link', 'kaggle')"


def upgrade() -> None:
    with op.batch_alter_table("pool_entries") as batch:
        batch.drop_constraint(op.f("ck_pool_entries_source_known"), type_="check")
        batch.create_check_constraint(op.f("ck_pool_entries_source_known"), SOURCES_AFTER)


def downgrade() -> None:
    kaggle_entries = op.get_bind().scalar(sa.text("SELECT count(*) FROM pool_entries WHERE source = 'kaggle'"))
    if kaggle_entries:
        raise RuntimeError("Remove Kaggle datasets before downgrading; existing imports were not deleted.")
    with op.batch_alter_table("pool_entries") as batch:
        batch.drop_constraint(op.f("ck_pool_entries_source_known"), type_="check")
        batch.create_check_constraint(op.f("ck_pool_entries_source_known"), SOURCES_BEFORE)

"""add cards.topic — a single coarse bucket, tags stay the fine-grained multi-value list"""
from alembic import op

revision = "0002_card_topic"
down_revision = "0001_postgres_multi_user"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE cards ADD COLUMN topic TEXT;
    CREATE INDEX cards_user_topic_idx ON cards(user_id, topic) WHERE topic IS NOT NULL;
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS cards_user_topic_idx; ALTER TABLE cards DROP COLUMN IF EXISTS topic;")

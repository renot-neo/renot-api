"""encrypt_bot_token_and_webhook_secret

Revision ID: b4f9003b49ad
Revises: 2c5141f25fd2
Create Date: 2026-08-12 01:40:07.984348

`Bot.token`/`Bot.webhook_secret` move from plaintext to Fernet-encrypted
storage - see `private/specs/2026-08-12-bot-secret-encryption-design.md`
for the full design. `token_last_four` becomes a real stored column
(previously a derived Python `@property`) since it can no longer be sliced
from ciphertext.

New columns are added nullable first, then backfilled from the existing
plaintext values (encrypted via `core.security.encrypt_secret`), THEN
altered to NOT NULL - a straight `nullable=False` add would fail outright
against any existing row (per this project's convention, migrations must
be written correctly regardless of current row count, even though no real
production data exists yet at the time this was written - confirmed with
the user before designing). Downgrade does the mirror: decrypt back into
plaintext columns before dropping the encrypted ones - needs the same
`TELEGRAM__TOKEN_ENCRYPTION_KEY` still configured, which it will be (same
`Settings` instance this migration already runs under).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4f9003b49ad"
down_revision: str | None = "2c5141f25fd2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_bots_table = sa.table(
    "bots",
    sa.column("id", sa.UUID()),
    sa.column("token", sa.Text()),
    sa.column("webhook_secret", sa.String()),
    sa.column("token_encrypted", sa.Text()),
    sa.column("token_last_four", sa.String()),
    sa.column("webhook_secret_encrypted", sa.String()),
)


def upgrade() -> None:
    from app.core.security import encrypt_secret

    op.add_column("bots", sa.Column("token_encrypted", sa.Text(), nullable=True))
    op.add_column("bots", sa.Column("token_last_four", sa.String(length=4), nullable=True))
    op.add_column(
        "bots", sa.Column("webhook_secret_encrypted", sa.String(length=255), nullable=True)
    )

    connection = op.get_bind()
    existing_rows = connection.execute(
        sa.select(_bots_table.c.id, _bots_table.c.token, _bots_table.c.webhook_secret)
    ).fetchall()
    for row in existing_rows:
        connection.execute(
            _bots_table.update()
            .where(_bots_table.c.id == row.id)
            .values(
                token_encrypted=encrypt_secret(row.token),
                token_last_four=row.token[-4:] if row.token else "",
                webhook_secret_encrypted=encrypt_secret(row.webhook_secret),
            )
        )

    op.alter_column("bots", "token_encrypted", nullable=False)
    op.alter_column("bots", "token_last_four", nullable=False)
    op.alter_column("bots", "webhook_secret_encrypted", nullable=False)

    op.drop_column("bots", "token")
    op.drop_column("bots", "webhook_secret")


def downgrade() -> None:
    from app.core.security import decrypt_secret

    op.add_column("bots", sa.Column("token", sa.Text(), nullable=True))
    op.add_column("bots", sa.Column("webhook_secret", sa.String(length=255), nullable=True))

    connection = op.get_bind()
    existing_rows = connection.execute(
        sa.select(
            _bots_table.c.id, _bots_table.c.token_encrypted, _bots_table.c.webhook_secret_encrypted
        )
    ).fetchall()
    for row in existing_rows:
        connection.execute(
            _bots_table.update()
            .where(_bots_table.c.id == row.id)
            .values(
                token=decrypt_secret(row.token_encrypted),
                webhook_secret=decrypt_secret(row.webhook_secret_encrypted),
            )
        )

    op.alter_column("bots", "token", nullable=False)
    op.alter_column("bots", "webhook_secret", nullable=False)

    op.drop_column("bots", "webhook_secret_encrypted")
    op.drop_column("bots", "token_last_four")
    op.drop_column("bots", "token_encrypted")

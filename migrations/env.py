import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.append(os.getcwd())

from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402
from app.efirma import models as efirma_models  # noqa: E402,F401
from app.rfc_clients import models as rfc_clients_models  # noqa: E402,F401
from app.sat_downloads import models as sat_downloads_models  # noqa: E402,F401
from app.users import models as users_models  # noqa: E402,F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

from logging.config import fileConfig
import sys
import os
from os.path import abspath, dirname

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from database import db
import models 

# Asegurar que Alembic encuentre tu archivo app.py en el path de Python
sys.path.insert(0, abspath(dirname(dirname(__file__))))
from app import app # Importamos tu app configurada con Supabase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    # CAMBIO: En vez de leer el archivo .ini, lee la URL de tu app.py (Supabase)
    url = app.config['SQLALCHEMY_DATABASE_URI']
    
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section) or {}
    # CAMBIO: Asegura inyectar la URL de Supabase desde la app de Flask
    configuration["sqlalchemy.url"] = app.config["SQLALCHEMY_DATABASE_URI"]

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
   
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
"""Crea el usuario administrador inicial para poder iniciar sesión.

Uso:
    python -m scripts.seed_admin

Variables de entorno:
    ADMIN_EMAIL    (default: admin@konyeka.com)
    ADMIN_PASSWORD (obligatorio si el usuario no existe)
    ADMIN_NOMBRE   (default: Administrador KONYEKA)
"""
import os
import sys

sys.path.append(os.getcwd())

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.users.service import create_user, get_user_by_email  # noqa: E402


def main():
    email = os.getenv("ADMIN_EMAIL", "admin@konyeka.com")
    password = os.getenv("ADMIN_PASSWORD")
    nombre = os.getenv("ADMIN_NOMBRE", "Administrador KONYEKA")

    if not password:
        raise SystemExit("Define ADMIN_PASSWORD en tu .env antes de correr este script.")

    db = SessionLocal()
    try:
        existing = get_user_by_email(db, email)
        if existing:
            print(f"El usuario {email} ya existe (id={existing.id}). No se hizo nada.")
            return
        user = create_user(db, email=email, nombre=nombre, password_hash=hash_password(password))
        print(f"Usuario administrador creado: {user.email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()

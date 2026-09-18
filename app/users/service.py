from sqlalchemy.orm import Session

from app.users.models import User


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email.lower().strip()).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, *, email: str, nombre: str, password_hash: str, rol: str = "admin") -> User:
    user = User(email=email.lower().strip(), nombre=nombre, password_hash=password_hash, rol=rol)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

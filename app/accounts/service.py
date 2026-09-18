from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.users.models import User
from app.users.service import get_user_by_email


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not user.activo:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def build_token_for(user: User) -> str:
    return create_access_token(subject=str(user.id), extra_claims={"rol": user.rol})

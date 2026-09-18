from fastapi import APIRouter, Depends

from app.core.deps import get_current_user
from app.users.models import User
from app.users.schemas import UserOut

router = APIRouter(prefix="/users", tags=["Usuarios"])


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.accounts.schemas import LoginRequest, TokenResponse
from app.accounts.service import authenticate, build_token_for
from app.core.db import get_db

router = APIRouter(prefix="/accounts", tags=["Sesión"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Correo o contraseña incorrectos")

    token = build_token_for(user)
    return TokenResponse(access_token=token, user=user)

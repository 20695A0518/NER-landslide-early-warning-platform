"""Authentication and account management."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)
from app.models.enums import Role
from app.models.user import User
from app.schemas.models import TokenResponse, UserCreate, UserOut, UserPreferences

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[Session, Depends(get_db)]


def _issue(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.username, str(user.role)),
        expires_in_minutes=settings.access_token_expire_minutes,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
def login(db: DbSession, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    user = db.query(User).filter(User.username == form.username).first()
    # Verify even when the user is missing, so a wrong username and a wrong
    # password take the same time and cannot be told apart by timing.
    placeholder = "$2b$12$" + "." * 53
    if not verify_password(form.password, user.hashed_password if user else placeholder):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")
    return _issue(user)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(db: DbSession, payload: UserCreate):
    """Public self-registration, restricted to citizen accounts.

    Official roles carry jurisdiction-wide visibility and the ability to verify
    reports, so they are provisioned by an administrator rather than claimed.
    """
    if payload.role != Role.CITIZEN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only citizen accounts can self-register; contact your district administrator",
        )
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken")

    data = payload.model_dump(exclude={"password"})
    user = User(**data, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return _issue(user)


@router.get("/me", response_model=UserOut)
def me(user: Annotated[User, Depends(get_current_user)]):
    return user


@router.patch("/me", response_model=UserOut)
def update_preferences(
    db: DbSession,
    payload: UserPreferences,
    user: Annotated[User, Depends(get_current_user)],
):
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbSession, _admin=Depends(require_roles(Role.ADMIN))):
    return db.query(User).order_by(User.id).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(db: DbSession, payload: UserCreate, _admin=Depends(require_roles(Role.ADMIN))):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "That username is already taken")
    data = payload.model_dump(exclude={"password"})
    user = User(**data, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

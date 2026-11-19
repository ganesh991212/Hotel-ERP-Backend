from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import platform_session, tenant_session
from app.core.deps import Ctx, HotelCtx, claims_from_header, count, load_principal
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.core.security import (
    hash_password,
    make_access_token,
    new_opaque_token,
    now,
    sha256,
    verify_password,
)
from app.iam.permissions import LOCKED_ROLE, PERMISSIONS
from app.models import AuditLog, ChefProfile, Hotel, Outlet, RefreshToken, Role, RolePermission, User

router = APIRouter(tags=["auth"])
staff = APIRouter(tags=["staff"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    platform: bool


async def _issue(db, user: User, family_id: UUID | None = None) -> TokenOut:
    raw = new_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid4(),
            token_hash=sha256(raw),
            expires_at=now() + timedelta(days=get_settings().refresh_token_days),
        )
    )
    return TokenOut(
        access_token=make_access_token(user.id, user.hotel_id, user.is_platform_admin),
        refresh_token=raw,
        platform=user.is_platform_admin,
    )


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn) -> TokenOut:
    # Pre-auth lookup across tenants needs the platform role (no tenant context exists yet).
    async with platform_session() as db:
        user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
        if not user or user.status != "ACTIVE" or not verify_password(user.password_hash, body.password):
            raise ApiError(401, "invalid_credentials", "Email or password is wrong")
        if user.hotel_id:
            hotel = await db.get(Hotel, user.hotel_id)
            if hotel is None:
                raise ApiError(401, "invalid_credentials", "Email or password is wrong")
        return await _issue(db, user)


@router.post("/auth/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn) -> TokenOut:
    """Rotate on every use. Reusing a used token revokes the whole family (rule 9)."""
    async with platform_session() as db:
        tok = (
            await db.execute(
                select(RefreshToken).where(RefreshToken.token_hash == sha256(body.refresh_token)).with_for_update()
            )
        ).scalar_one_or_none()
        if not tok or tok.revoked_at or tok.expires_at < now():
            raise ApiError(401, "invalid_refresh", "Refresh token is not valid")
        if tok.used_at:
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == tok.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now())
            )
            await db.commit()
            raise ApiError(401, "refresh_reused", "Refresh token reuse detected; please sign in again")
        tok.used_at = now()
        user = await db.get(User, tok.user_id)
        if not user or user.status != "ACTIVE":
            raise ApiError(401, "invalid_refresh", "Refresh token is not valid")
        return await _issue(db, user, tok.family_id)


@router.post("/auth/logout", status_code=204)
async def logout(body: RefreshIn) -> None:
    async with platform_session() as db:
        tok = (
            await db.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256(body.refresh_token)))
        ).scalar_one_or_none()
        if tok:
            await db.execute(
                update(RefreshToken).where(RefreshToken.family_id == tok.family_id).values(revoked_at=now())
            )


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


@router.post("/me/password", response_model=TokenOut)
async def change_password(body: PasswordChangeIn, claims: dict = Depends(claims_from_header)) -> TokenOut:
    """Any signed-in user, platform admin included. Every other device is signed out (their refresh tokens are
    revoked; open screens last until their 15-minute access token expires). This device gets fresh tokens."""
    async with platform_session() as db:
        user = await db.get(User, UUID(claims["sub"]), with_for_update=True)
        if not user or user.status != "ACTIVE":
            raise ApiError(401, "unauthenticated", "Unknown user")
        if not verify_password(user.password_hash, body.current_password):
            raise bad_request("wrong_password", "Current password is wrong")
        if body.new_password == body.current_password:
            raise bad_request("same_password", "Choose a password you have not just used")
        user.password_hash = hash_password(body.new_password)
        await _sign_out_everywhere(db, user.id)
        db.add(
            AuditLog(
                hotel_id=user.hotel_id,
                actor_user_id=user.id,
                action="user.password_change",
                entity="user",
                entity_id=str(user.id),
            )
        )
        return await _issue(db, user)


async def _sign_out_everywhere(db, user_id: UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )


@router.get("/me")
async def me(claims: dict = Depends(claims_from_header)) -> dict:
    if claims.get("plt"):
        async with platform_session() as db:
            u = await db.get(User, UUID(claims["sub"]))
            if not u:
                raise ApiError(401, "unauthenticated", "Unknown user")
            return {
                "id": str(u.id),
                "name": u.name,
                "email": u.email,
                "platform": True,
                "role": "PLATFORM_ADMIN",
                "permissions": [],
                "hotel": None,
                "outlet": None,
            }
    async with tenant_session(UUID(claims["hid"])) as db:
        p = await load_principal(db, claims)
        u = await db.get(User, p.user_id)
        hotel = await db.get(Hotel, p.hotel_id)
        outlet = (await db.execute(select(Outlet).order_by(Outlet.created_at).limit(1))).scalar_one_or_none()
        return {
            "id": str(u.id),
            "name": u.name,
            "email": u.email,
            "platform": False,
            "role": p.role_code,
            "permissions": sorted(p.permissions),
            "hotel": {"id": str(hotel.id), "name": hotel.name} if hotel else None,
            "outlet": {
                "id": str(outlet.id),
                "name": outlet.name,
                "kitchen_mode": outlet.kitchen_mode,
                "serve_mode": outlet.serve_mode,
            }
            if outlet
            else None,
        }


@router.get("/me/entitlements")
async def my_entitlements(ctx: Ctx = HotelCtx()) -> dict:
    """Resolved map for the UI. The UI hides locked features; the server stays the authority."""
    return ctx.entitlements


# ------------------------------------------------------------------------ staff --


class UserOut(BaseModel):
    id: UUID
    name: str
    email: str
    phone: str | None
    status: str
    role_id: UUID | None
    role_code: str | None = None
    role_name: str | None = None
    on_shift: bool | None = None


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=200)
    role_id: UUID


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    status: str | None = Field(default=None, pattern="^(ACTIVE|DISABLED)$")
    role_id: UUID | None = None


async def _user_out(ctx: Ctx, u: User) -> UserOut:
    role = await ctx.db.get(Role, u.role_id) if u.role_id else None
    chef = await ctx.db.get(ChefProfile, u.id)
    return UserOut(
        id=u.id,
        name=u.name,
        email=u.email,
        phone=u.phone,
        status=u.status,
        role_id=u.role_id,
        role_code=role.code if role else None,
        role_name=role.name if role else None,
        on_shift=chef.on_shift if chef else None,
    )


@staff.get("/users", response_model=list[UserOut])
async def list_users(ctx: Ctx = HotelCtx("staff.manage")) -> list[UserOut]:
    users = (await ctx.db.execute(select(User).order_by(User.created_at))).scalars().all()
    return [await _user_out(ctx, u) for u in users]


async def _ensure_chef_limit(ctx: Ctx, role: Role) -> None:
    if role.code == "CHEF":
        await ctx.require_limit("MAX_CHEFS", count(ChefProfile))


@staff.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserIn, ctx: Ctx = HotelCtx("staff.manage")) -> UserOut:
    await ctx.require_limit("MAX_STAFF", count(User, User.status == "ACTIVE"))
    role = await ctx.db.get(Role, body.role_id)
    if not role:
        raise not_found("Role")
    await _ensure_chef_limit(ctx, role)
    # Email is globally unique; check via platform role so we do not leak which hotel owns it.
    async with platform_session() as pdb:
        if (await pdb.execute(select(User.id).where(User.email == body.email.lower()))).first():
            raise conflict("email_taken", "This email is already registered")
    u = User(
        hotel_id=ctx.hotel_id,
        role_id=role.id,
        name=body.name,
        email=body.email.lower(),
        phone=body.phone,
        password_hash=hash_password(body.password),
    )
    ctx.db.add(u)
    await ctx.db.flush()
    if role.code == "CHEF":
        outlet = (await ctx.db.execute(select(Outlet).order_by(Outlet.created_at).limit(1))).scalar_one()
        ctx.db.add(ChefProfile(user_id=u.id, hotel_id=ctx.hotel_id, outlet_id=outlet.id))
    await ctx.audit("user.create", "user", u.id, after={"email": u.email, "role": role.code})
    return await _user_out(ctx, u)


@staff.patch("/users/{user_id}", response_model=UserOut)
async def patch_user(user_id: UUID, body: UserPatch, ctx: Ctx = HotelCtx("staff.manage")) -> UserOut:
    u = await ctx.db.get(User, user_id)
    if not u:
        raise not_found("User")
    if u.id == ctx.user_id and (body.status == "DISABLED" or body.role_id):
        raise bad_request("self_lockout", "You cannot disable yourself or change your own role")
    before = {"status": u.status, "role_id": str(u.role_id)}
    if body.role_id and body.role_id != u.role_id:
        role = await ctx.db.get(Role, body.role_id)
        if not role:
            raise not_found("Role")
        await _ensure_chef_limit(ctx, role)
        u.role_id = role.id
        chef = await ctx.db.get(ChefProfile, u.id)
        if role.code == "CHEF" and not chef:
            outlet = (await ctx.db.execute(select(Outlet).order_by(Outlet.created_at).limit(1))).scalar_one()
            ctx.db.add(ChefProfile(user_id=u.id, hotel_id=ctx.hotel_id, outlet_id=outlet.id))
    for f in ("name", "phone", "status"):
        v = getattr(body, f)
        if v is not None:
            setattr(u, f, v)
    await ctx.audit("user.update", "user", u.id, before=before, after={"status": u.status, "role_id": str(u.role_id)})
    return await _user_out(ctx, u)


class PasswordResetIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


@staff.post("/users/{user_id}/password", status_code=204)
async def reset_password(user_id: UUID, body: PasswordResetIn, ctx: Ctx = HotelCtx("staff.manage")) -> None:
    """For staff who forgot their password. Signs them out everywhere. No escalation: only a hotel admin can reset
    a hotel admin's password (otherwise a manager could take over the admin account)."""
    u = await ctx.db.get(User, user_id)
    if not u:
        raise not_found("User")
    if u.id == ctx.user_id:
        raise bad_request("use_change_password", "Change your own password from your profile")
    role = await ctx.db.get(Role, u.role_id) if u.role_id else None
    if role is not None and role.code == LOCKED_ROLE and ctx.principal.role_code != LOCKED_ROLE:
        raise forbidden("permission_denied", "Only a hotel admin can reset a hotel admin's password")
    u.password_hash = hash_password(body.new_password)
    async with platform_session() as pdb:  # refresh tokens are the auth store, outside tenant scope
        await _sign_out_everywhere(pdb, u.id)
    await ctx.audit("user.password_reset", "user", u.id)


# ------------------------------------------------------------------------ roles --


class RoleOut(BaseModel):
    id: UUID
    code: str
    name: str
    is_system: bool
    locked: bool
    permissions: list[str]


class RolePermsIn(BaseModel):
    permissions: list[str]


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    permissions: list[str] = []


@staff.get("/permissions")
async def list_permissions(ctx: Ctx = HotelCtx()) -> list[dict]:
    return [{"key": k, "description": v} for k, v in PERMISSIONS.items()]


async def _role_out(ctx: Ctx, r: Role) -> RoleOut:
    perms = (await ctx.db.execute(select(RolePermission.permission).where(RolePermission.role_id == r.id))).scalars()
    return RoleOut(
        id=r.id,
        code=r.code,
        name=r.name,
        is_system=r.is_system,
        locked=r.code == LOCKED_ROLE,
        permissions=sorted(perms),
    )


@staff.get("/roles", response_model=list[RoleOut])
async def list_roles(ctx: Ctx = HotelCtx("staff.manage")) -> list[RoleOut]:
    roles = (await ctx.db.execute(select(Role).order_by(Role.is_system.desc(), Role.name))).scalars().all()
    return [await _role_out(ctx, r) for r in roles]


def _validate_perms(perms: list[str]) -> set[str]:
    unknown = set(perms) - set(PERMISSIONS)
    if unknown:
        raise bad_request("unknown_permission", "Unknown permissions", {"unknown": sorted(unknown)})
    return set(perms)


@staff.put("/roles/{role_id}/permissions", response_model=RoleOut)
async def set_role_permissions(role_id: UUID, body: RolePermsIn, ctx: Ctx = HotelCtx("role.manage")) -> RoleOut:
    role = await ctx.db.get(Role, role_id)
    if not role:
        raise not_found("Role")
    if role.code == LOCKED_ROLE:
        raise forbidden("role_locked", "Hotel admin always has every permission")
    new = _validate_perms(body.permissions)
    old = set(
        (await ctx.db.execute(select(RolePermission.permission).where(RolePermission.role_id == role.id))).scalars()
    )
    for p in old - new:
        await ctx.db.delete(await ctx.db.get(RolePermission, (role.id, p)))
    for p in new - old:
        ctx.db.add(RolePermission(role_id=role.id, permission=p, hotel_id=ctx.hotel_id))
    await ctx.audit(
        "role.permissions", "role", role.id, before={"permissions": sorted(old)}, after={"permissions": sorted(new)}
    )
    await ctx.db.flush()
    return await _role_out(ctx, role)


@staff.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleIn, ctx: Ctx = HotelCtx("role.manage", feature="CUSTOM_ROLES")) -> RoleOut:
    perms = _validate_perms(body.permissions)
    code = "CUSTOM_" + uuid4().hex[:8].upper()
    role = Role(hotel_id=ctx.hotel_id, code=code, name=body.name, is_system=False)
    ctx.db.add(role)
    await ctx.db.flush()
    for p in perms:
        ctx.db.add(RolePermission(role_id=role.id, permission=p, hotel_id=ctx.hotel_id))
    await ctx.audit("role.create", "role", role.id, after={"name": role.name, "permissions": sorted(perms)})
    await ctx.db.flush()
    return await _role_out(ctx, role)

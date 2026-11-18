"""Seed plans, a platform admin and (optionally) a demo hotel. Local/dev use.

  python -m app.seed                     plans only
  PLATFORM_ADMIN_EMAIL=... PLATFORM_ADMIN_PASSWORD=... python -m app.seed
  python -m app.seed --demo              also a demo hotel with sample (fictional) menu and staff

Demo passwords are random unless DEMO_PASSWORD is set; they are printed once. Nothing here is a secret.
"""

import asyncio
import os
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password, new_opaque_token
from app.models import ChefProfile, DiningTable, MenuCategory, MenuItem, Outlet, Plan, Role, User
from app.platform.provision import provision_hotel, seed_plans

DEMO_MENU = {
    "Starters": [
        ("Paneer Tikka", 24000, True, 15),
        ("Chicken 65", 26000, False, 12),
        ("Veg Spring Roll", 18000, True, 10),
    ],
    "Mains": [
        ("Veg Biryani", 28000, True, 20),
        ("Chicken Biryani", 34000, False, 22),
        ("Butter Naan", 6000, True, 6),
        ("Dal Tadka", 20000, True, 12),
    ],
    "Beverages": [("Masala Chai", 4000, True, 4), ("Fresh Lime Soda", 9000, True, 3)],
    "Desserts": [("Gulab Jamun", 9000, True, 3)],
}


async def main(demo: bool) -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db, db.begin():
        await seed_plans(db)
        email = os.environ.get("PLATFORM_ADMIN_EMAIL")
        if email:
            pw = os.environ.get("PLATFORM_ADMIN_PASSWORD") or secrets.token_urlsafe(10)
            if not (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none():
                db.add(
                    User(
                        name="Platform admin",
                        email=email.lower(),
                        password_hash=hash_password(pw),
                        is_platform_admin=True,
                    )
                )
                print(f"platform admin: {email} / {pw}")
        if demo:
            if (await db.execute(select(User).where(User.email == "admin@demo.example.com"))).first():
                print("demo hotel exists")
            else:
                pw = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(10)
                plan = (await db.execute(select(Plan).where(Plan.code == "GROWTH"))).scalar_one()
                hotel, _ = await provision_hotel(
                    db,
                    name="Demo Bistro",
                    plan=plan,
                    admin_name="Asha (admin)",
                    admin_email="admin@demo.example.com",
                    admin_password=pw,
                    city="Hyderabad",
                )
                outlet = (await db.execute(select(Outlet).where(Outlet.hotel_id == hotel.id))).scalar_one()
                roles = {r.code: r for r in (await db.execute(select(Role).where(Role.hotel_id == hotel.id))).scalars()}
                for name, email_, code in [
                    ("Ravi", "ravi@demo.example.com", "CHEF"),
                    ("Sita", "sita@demo.example.com", "CHEF"),
                    ("Kiran", "kiran@demo.example.com", "WAITER"),
                ]:
                    u = User(
                        hotel_id=hotel.id,
                        role_id=roles[code].id,
                        name=name,
                        email=email_,
                        password_hash=hash_password(pw),
                    )
                    db.add(u)
                    await db.flush()
                    if code == "CHEF":
                        db.add(ChefProfile(user_id=u.id, hotel_id=hotel.id, outlet_id=outlet.id))
                for i, (label, area) in enumerate(
                    [
                        ("T1", "Indoor"),
                        ("T2", "Indoor"),
                        ("T3", "Indoor"),
                        ("T4", "Indoor"),
                        ("P1", "Patio"),
                        ("P2", "Patio"),
                    ]
                ):
                    db.add(
                        DiningTable(
                            hotel_id=hotel.id,
                            outlet_id=outlet.id,
                            label=label,
                            area=area,
                            seats=4 if i < 4 else 2,
                            qr_token=new_opaque_token(18),
                        )
                    )
                for sort, (cat, items) in enumerate(DEMO_MENU.items()):
                    c = MenuCategory(hotel_id=hotel.id, outlet_id=outlet.id, name=cat, sort=sort)
                    db.add(c)
                    await db.flush()
                    for name, price, veg, mins in items:
                        db.add(
                            MenuItem(
                                hotel_id=hotel.id,
                                category_id=c.id,
                                name=name,
                                price_minor=price,
                                is_veg=veg,
                                est_prep_minutes=mins,
                            )
                        )
                print(
                    "demo hotel: admin@, ravi@, sita@, kiran@demo.example.com / " + pw
                )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--demo" in sys.argv))

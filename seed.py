"""
Prepares a fresh database. Safe to run on every deploy:
- Demo menu and default cafe settings are created only when the database is empty.
- The first manager account comes from the environment variables
  INITIAL_ADMIN_USERNAME and INITIAL_ADMIN_PASSWORD, and only when no staff account exists.
Existing data is never deleted.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from menu.models import MenuCategory, MenuItem, MenuTab, MenuSection
from accounts.models import StaffUser, CafeConfiguration
from django.contrib.auth.hashers import make_password


def seed_demo_menu():
    # 1.3 Create Custom Tabs and Sections
    tab_drinks = MenuTab.objects.create(name_en="Drinks", name_ar="مشروبات", order_index=1)
    tab_food = MenuTab.objects.create(name_en="Food & Pastries", name_ar="مأكولات ومعجنات", order_index=2)

    sec_espresso = MenuSection.objects.create(
        tab=tab_drinks,
        name_en="Espresso Specialty Bar",
        name_ar="☕ قهوة إسبريسو مختصة",
        banner_image="https://images.unsplash.com/photo-1497935586351-b67a49e012bf?q=80&w=1000",
        order_index=1
    )
    sec_cold = MenuSection.objects.create(
        tab=tab_drinks,
        name_en="Refreshing & Cold Brews",
        name_ar="🍹 المشروبات الباردة والمنعشة",
        banner_image="https://images.unsplash.com/photo-1517701604599-bb29b565090c?q=80&w=1000",
        order_index=2
    )
    sec_bakery = MenuSection.objects.create(
        tab=tab_food,
        name_en="Artisanal Bakery & Desserts",
        name_ar="🥐 مخبوزات وحلويات فاخرة",
        banner_image="https://images.unsplash.com/photo-1555507036-ab1f4038808a?q=80&w=1000",
        order_index=1
    )

    # 1.5 Create default Cafe Configuration
    CafeConfiguration.objects.create(
        phone_number="+962 79 262 6262",
        whatsapp_number="962792626262",
        email="hello@26cafe.com",
        location_desc_en="Wadi Musa, Petra Road - Near Petra Visitor Center",
        location_desc_ar="وادي موسى، شارع البتراء - بالقرب من مركز زوار البتراء",
        latitude=30.3222,
        longitude=35.4795,
        opening_hours_en="Daily: 7:30 AM - 11:30 PM",
        opening_hours_ar="يومياً: 7:30 صباحاً - 11:30 مساءً",
        instagram_url="https://instagram.com/26cafe",
        facebook_url="https://facebook.com/26cafe",
        tiktok_url="https://tiktok.com/@26cafe"
    )

    # 2. Create categories
    cat_espresso = MenuCategory.objects.create(
        loyverse_category_id="cat-esp",
        pos_category_name="Espresso Drinks",
        display_category_name="☕ Espresso Specialty Bar",
        category_image="https://images.unsplash.com/photo-151097252790b-af4f902673d1?q=80&w=600",
        order_index=1
    )

    cat_bakery = MenuCategory.objects.create(
        loyverse_category_id="cat-bak",
        pos_category_name="Bakeries",
        display_category_name="🥐 Artisanal Bakeries",
        category_image="https://images.unsplash.com/photo-1555507036-ab1f4038808a?q=80&w=600",
        order_index=2
    )

    cat_cold = MenuCategory.objects.create(
        loyverse_category_id="cat-cld",
        pos_category_name="Cold Brews",
        display_category_name="🍹 Refreshing & Cold Brews",
        category_image="https://images.unsplash.com/photo-1517701604599-bb29b565090c?q=80&w=600",
        order_index=3
    )

    # 3. Create items
    MenuItem.objects.create(
        loyverse_id="item-esp-double",
        pos_name="Double Espresso",
        display_name="Signature Double Espresso",
        description="Intense and aromatic double shot extracted from premium organic house-blend coffee beans.",
        price=2.250,
        category=cat_espresso,
        custom_section=sec_espresso,
        status="AVAILABLE",
        order_index=1,
        is_hot=True,
        is_coffee=True,
        is_dairy_free=True
    )

    MenuItem.objects.create(
        loyverse_id="item-esp-latte",
        pos_name="Caffe Latte",
        display_name="Spanish Latte Warm",
        description="Smooth double shot espresso combined with textured steamed milk and a hint of organic sweetness.",
        price=3.500,
        category=cat_espresso,
        custom_section=sec_espresso,
        status="AVAILABLE",
        order_index=2,
        is_hot=True,
        is_coffee=True,
        is_milky=True
    )

    MenuItem.objects.create(
        loyverse_id="item-esp-cap",
        pos_name="Cappuccino",
        display_name="Foamy Cappuccino Gold",
        description="Classic espresso balanced with equal parts steamed milk and deep, rich milk foam dusted with cocoa.",
        price=3.200,
        category=cat_espresso,
        custom_section=sec_espresso,
        status="AUTOMATIC",
        loyverse_stock=8,
        order_index=3,
        is_hot=True,
        is_coffee=True,
        is_milky=True
    )

    MenuItem.objects.create(
        loyverse_id="item-bak-cro",
        pos_name="Croissant Butter",
        display_name="Golden Butter Croissant",
        description="Flaky, multi-layered French pastry baked to a golden crisp with real normandy butter.",
        price=2.800,
        category=cat_bakery,
        custom_section=sec_bakery,
        status="AUTOMATIC",
        loyverse_stock=2,
        order_index=1,
        is_hot=True,
        is_milky=True
    )

    MenuItem.objects.create(
        loyverse_id="item-bak-cookies",
        pos_name="Choc Chip Cookie",
        display_name="Fudge Chocolate Cookie",
        description="Soft-baked cookie packed with Belgian dark chocolate chunks and topped with sea salt flakes.",
        price=2.000,
        category=cat_bakery,
        custom_section=sec_bakery,
        status="OUT_OF_STOCK",
        loyverse_stock=0,
        order_index=2,
        is_hot=True,
        is_milky=True
    )

    MenuItem.objects.create(
        loyverse_id="item-cld-matcha",
        pos_name="Iced Matcha",
        display_name="Iced Ceremonial Matcha",
        description="Vibrant ceremonial grade Japanese matcha whisked with cold milk and served over ice.",
        price=4.200,
        category=cat_cold,
        custom_section=sec_cold,
        status="AVAILABLE",
        order_index=1,
        is_cold=True,
        is_milky=True
    )


if MenuTab.objects.exists() or MenuItem.objects.exists() or CafeConfiguration.objects.exists():
    print("Seed: database already has data, demo menu skipped.")
else:
    seed_demo_menu()
    print("Seed: demo menu and default cafe settings created.")

if StaffUser.objects.exists():
    print("Seed: staff accounts already exist, nothing changed.")
else:
    admin_username = os.environ.get("INITIAL_ADMIN_USERNAME", "").strip()
    admin_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
    if admin_username and len(admin_password) >= 8:
        StaffUser.objects.create(
            username=admin_username,
            password_hash=make_password(admin_password),
            employee_name="Manager",
            role="ADMIN"
        )
        print(f"Seed: manager account '{admin_username}' created from environment variables.")
    else:
        print("Seed: WARNING no staff account exists. Set INITIAL_ADMIN_USERNAME and "
              "INITIAL_ADMIN_PASSWORD (8+ characters) and redeploy to create the manager account.")

import datetime
import jwt
from unittest import mock
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.test import TestCase, Client
from django.conf import settings
from django.urls import reverse
from accounts.models import CustomerProfile, StaffUser, CafeConfiguration, LoyaltyReward, RaffleTicket
from accounts.views import loyverse_webhook_key
from cafe_smart.integrations import LoyverseClient

class AccountsTests(TestCase):
    def setUp(self):
        cache.clear()
        # Create standard customer profile
        self.customer = CustomerProfile.objects.create(
            loyverse_customer_id="cust-1234",
            phone_number="+962791112233",
            password_hash="hashed_password",
            full_name="Ahmad Salem",
            birth_date=datetime.date(1990, 5, 15),
            is_verified=True
        )

    def test_age_compliance_validation(self):
        """
        Tests that users under 18 cannot initiate registration.
        """
        client = Client()
        
        # Test under-age (17 years old)
        today = datetime.date.today()
        under_age_birthdate = datetime.date(today.year - 17, today.month, today.day).strftime("%Y-%m-%d")
        
        response = client.post(reverse('accounts:customer_register'), {
            "phone_number": "+962790000000",
            "full_name": "Young Customer",
            "birth_date": under_age_birthdate,
            "password": "password123"
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("Compliance error", response.json()["message"])

        # Test over-age (18 years old)
        over_age_birthdate = datetime.date(today.year - 18, today.month, today.day).strftime("%Y-%m-%d")
        response = client.post(reverse('accounts:customer_register'), {
            "phone_number": "+962799999999",
            "full_name": "Adult Customer",
            "birth_date": over_age_birthdate,
            "password": "password123"
        })
        self.assertEqual(response.status_code, 200)

    def test_dynamic_qr_code_token_security(self):
        """
        Verifies signed token generation, payload contents, and signature validation.
        """
        client = Client()
        
        # Login customer by mock setting session variables
        session = client.session
        session["customer_id"] = self.customer.id
        session["loyverse_customer_id"] = self.customer.loyverse_customer_id
        session.save()

        response = client.get(reverse('accounts:get_qr_token'))
        self.assertEqual(response.status_code, 200)
        
        token = response.json()["token"]
        self.assertTrue(token)
        
        # Decode and verify using settings SECRET_KEY
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        self.assertEqual(payload["loyverse_customer_id"], self.customer.loyverse_customer_id)
        self.assertIn("timestamp", payload)
        self.assertIn("exp", payload)

    def test_customer_forgot_password_and_reset(self):
        """
        Tests forgot password OTP request and reset verification flows.
        """
        client = Client()
        
        # 1. Request OTP for unregistered number - should return 404
        response = client.post(reverse('accounts:customer_forgot_password_request'), {
            "phone_number": "+962790001111"
        })
        self.assertEqual(response.status_code, 404)
        
        # 2. Request OTP for registered number
        response = client.post(reverse('accounts:customer_forgot_password_request'), {
            "phone_number": "+962791112233"
        })
        self.assertEqual(response.status_code, 200)
        
        # Retrieve OTP code from session
        session = client.session
        otp_code = session.get("reset_otp")
        self.assertTrue(otp_code)
        
        # 3. Reset password using invalid OTP - should fail
        response = client.post(reverse('accounts:customer_forgot_password_reset'), {
            "phone_number": "+962791112233",
            "otp": "000000",
            "new_password": "new_secure_password"
        })
        self.assertEqual(response.status_code, 400)
        
        # 4. Reset password using valid OTP
        response = client.post(reverse('accounts:customer_forgot_password_reset'), {
            "phone_number": "+962791112233",
            "otp": otp_code,
            "new_password": "new_secure_password"
        })
        self.assertEqual(response.status_code, 200)
        
        # 5. Try login with new password
        response = client.post(reverse('accounts:customer_login'), {
            "phone_number": "+962791112233",
            "password": "new_secure_password"
        })
        self.assertEqual(response.status_code, 200)

    def test_otp_attempt_limit_lockout(self):
        """
        Verifies that exceeding 5 failed OTP verification attempts locks out the session.
        """
        client = Client()
        
        # Request forgot password OTP
        client.post(reverse('accounts:customer_forgot_password_request'), {
            "phone_number": "+962791112233"
        })
        
        # Attempt 5 incorrect verifications
        for i in range(5):
            res = client.post(reverse('accounts:customer_forgot_password_reset'), {
                "phone_number": "+962791112233",
                "otp": f"99999{i}",
                "new_password": "some_password"
            })
            self.assertEqual(res.status_code, 400)
            
        # 6th attempt should trigger 429 lockout
        res6 = client.post(reverse('accounts:customer_forgot_password_reset'), {
            "phone_number": "+962791112233",
            "otp": "000000",
            "new_password": "some_password"
        })
        self.assertEqual(res6.status_code, 429)

    def webhook_url(self):
        return reverse('accounts:loyverse_webhook') + f"?key={loyverse_webhook_key()}"

    def test_loyverse_webhook_requires_key(self):
        payload = {"receipts": [{"receipt_number": "1-1001", "customer_id": "cust-1234", "total_money": 20, "points_earned": 20}]}
        res = Client().post(reverse('accounts:loyverse_webhook'), data=payload, content_type='application/json')
        self.assertEqual(res.status_code, 403)
        res = Client().post(reverse('accounts:loyverse_webhook') + "?key=wrong", data=payload, content_type='application/json')
        self.assertEqual(res.status_code, 403)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 0)

    def test_loyverse_webhook_idempotency(self):
        """
        Verifies that duplicate webhook events with the same receipt_number are handled idempotently.
        """
        client = Client()
        payload = {
            "type": "receipts.update",
            "receipts": [{
                "customer_id": self.customer.loyverse_customer_id,
                "receipt_number": "LV-TEST-IDEMPOTENT-001",
                "receipt_type": "SALE",
                "total_money": 20.0,
                "points_earned": 20
            }]
        }

        # First submission - should credit points and raffle tickets
        res1 = client.post(self.webhook_url(), data=payload, content_type='application/json')
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json()["results"][0]["status"], "processed")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 20)
        self.assertEqual(RaffleTicket.objects.filter(customer=self.customer).count(), 4)

        # Second submission with same receipt_number - no double crediting
        res2 = client.post(self.webhook_url(), data=payload, content_type='application/json')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["results"][0]["status"], "duplicate")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 20)  # Points remain 20, not 40!
        self.assertEqual(RaffleTicket.objects.filter(customer=self.customer).count(), 4)

    def test_loyverse_webhook_verifies_receipt_with_api(self):
        """With a token configured, the receipt is re-read from Loyverse and forged values are ignored."""
        CafeConfiguration.objects.create(loyverse_api_token="tok")
        forged = {"receipts": [{"receipt_number": "2-2002", "customer_id": "cust-1234", "total_money": 999, "points_earned": 5000}]}
        real = {"receipt_number": "2-2002", "receipt_type": "SALE", "customer_id": "cust-1234",
                "total_money": 6.0, "points_earned": 6, "points_balance": 106}
        with mock.patch.object(LoyverseClient, "get_receipt", return_value=real):
            res = Client().post(self.webhook_url(), data=forged, content_type='application/json')
        self.assertEqual(res.json()["results"][0]["status"], "processed")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 106)

        unknown = {"receipts": [{"receipt_number": "9-9999", "customer_id": "cust-1234", "points_earned": 50}]}
        with mock.patch.object(LoyverseClient, "get_receipt", return_value=None):
            res = Client().post(self.webhook_url(), data=unknown, content_type='application/json')
        self.assertEqual(res.json()["results"][0]["status"], "ignored")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 106)

    def test_staff_login_has_no_default_accounts(self):
        res = Client().post(reverse('accounts:staff_login_api'), {"username": "admin", "password": "admin123"})
        self.assertEqual(res.status_code, 401)
        self.assertFalse(StaffUser.objects.exists())

    def test_staff_login_rate_limit_and_inactive(self):
        StaffUser.objects.create(username="boss", password_hash=make_password("correct-horse"), employee_name="Boss", role="ADMIN")
        StaffUser.objects.create(username="gone", password_hash=make_password("correct-horse"), employee_name="Gone", is_active=False)
        client = Client()
        self.assertEqual(client.post(reverse('accounts:staff_login_api'), {"username": "gone", "password": "correct-horse"}).status_code, 401)
        for _ in range(5):
            self.assertEqual(client.post(reverse('accounts:staff_login_api'), {"username": "boss", "password": "wrong"}).status_code, 401)
        # Locked even with the right password
        self.assertEqual(client.post(reverse('accounts:staff_login_api'), {"username": "boss", "password": "correct-horse"}).status_code, 429)
        cache.clear()
        res = client.post(reverse('accounts:staff_login_api'), {"username": "boss", "password": "correct-horse"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["redirect"], "/staff/dashboard/")

    def test_waiter_cannot_use_admin_tools(self):
        StaffUser.objects.create(username="w", password_hash=make_password("waiter-pass"), employee_name="W", role="WAITER")
        client = Client()
        client.post(reverse('accounts:staff_login_api'), {"username": "w", "password": "waiter-pass"})
        self.assertEqual(client.post(reverse('accounts:whatsapp_test_send'), {"phone_number": "0790000000"}).status_code, 401)
        self.assertEqual(client.post(reverse('accounts:staff_loyverse_sync_live')).status_code, 401)
        self.assertEqual(client.post(reverse('accounts:staff_rewards_manage'), {"action": "create", "name_ar": "x", "points_required": 5}).status_code, 401)

    def test_registration_links_customer_to_loyverse(self):
        CafeConfiguration.objects.create(loyverse_api_token="tok")
        client = Client()
        res = client.post(reverse('accounts:customer_register'), {
            "phone_number": "0795556677", "full_name": "Sara", "birth_date": "1995-01-01", "password": "secret1"})
        self.assertEqual(res.status_code, 200)
        otp = client.session["reg_otp"]
        created = {"id": "lv-uuid-1", "customer_code": None, "total_points": 0}
        with mock.patch.object(LoyverseClient, "find_customer_by_phone", return_value=None), \
             mock.patch.object(LoyverseClient, "save_customer", side_effect=lambda p: {**created, "customer_code": p["customer_code"]}) as save:
            res = client.post(reverse('accounts:customer_verify'), {"phone_number": "0795556677", "otp": otp})
        self.assertEqual(res.status_code, 200)
        sent = save.call_args[0][0]
        self.assertEqual(sent["phone_number"], "+962795556677")
        customer = CustomerProfile.objects.get(phone_number="+962795556677")
        self.assertEqual(customer.loyverse_customer_id, "lv-uuid-1")
        self.assertEqual(customer.barcode, sent["customer_code"])

    def test_loyalty_page_reads_points_from_loyverse_and_lists_rewards(self):
        CafeConfiguration.objects.create(loyverse_api_token="tok")
        self.customer.password_hash = make_password("secret1")
        self.customer.save()
        LoyaltyReward.objects.create(name_ar="قهوة مجانية", points_required=100)
        LoyaltyReward.objects.create(name_ar="حلى", points_required=300)
        LoyaltyReward.objects.create(name_ar="مخفية", points_required=10, is_active=False)
        client = Client()
        client.post(reverse('accounts:customer_login'), {"phone_number": "0791112233", "password": "secret1"})
        with mock.patch.object(LoyverseClient, "get_customer", return_value={"id": "cust-1234", "total_points": 150}):
            res = client.get(reverse('accounts:loyalty_view'))
        self.assertEqual(res.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 150)
        rewards = res.context["rewards"]
        self.assertEqual([r.name_ar for r in rewards], ["قهوة مجانية", "حلى"])
        self.assertTrue(rewards[0].is_available)
        self.assertEqual(rewards[1].points_remaining, 150)

    def test_customer_cannot_redeem_points_on_website(self):
        client = Client()
        session = client.session
        session["customer_id"] = self.customer.id
        session.save()
        res = client.post(reverse('accounts:customer_redeem_voucher'), {"points_cost": 1, "reward_details": "Big prize"})
        self.assertEqual(res.status_code, 410)

    def test_link_customer_reuses_existing_loyverse_customer_by_phone(self):
        CafeConfiguration.objects.create(loyverse_api_token="tok")
        self.customer.loyverse_customer_id = "PENDING-abc"
        self.customer.barcode = "WBC123456789"
        self.customer.save()
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET" and path == "/customers" and not kwargs["params"].get("cursor"):
                return {"customers": [{"id": "other", "phone_number": "0790000000"}], "cursor": "page2"}
            if method == "GET" and path == "/customers":
                return {"customers": [{"id": "lv-9", "phone_number": "079 111 2233", "customer_code": "", "total_points": 42}]}
            if method == "POST":
                return {"id": "lv-9", "customer_code": kwargs["json"]["customer_code"], "total_points": 42}

        with mock.patch.object(LoyverseClient, "_request", side_effect=fake_request):
            self.assertTrue(LoyverseClient.refresh_customer_points(self.customer))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.loyverse_customer_id, "lv-9")
        self.assertEqual(self.customer.points_balance, 42)
        self.assertEqual(self.customer.barcode, "WBC123456789")
        # Existing customer updated with our barcode, not duplicated
        posts = [c for c in calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["json"]["id"], "lv-9")

    def test_live_sync_reads_every_page_and_parks_new_items(self):
        from menu.models import MenuCategory, MenuItem, MenuTab, MenuSection
        StaffUser.objects.create(username="boss", password_hash=make_password("boss-pass1"), employee_name="B", role="ADMIN")
        CafeConfiguration.objects.create(loyverse_api_token="tok")
        cat = MenuCategory.objects.create(loyverse_category_id="c1", pos_category_name="Coffee", display_category_name="Coffee")
        section = MenuSection.objects.create(tab=MenuTab.objects.create(name_en="Hot", name_ar="ساخن"), name_en="Espresso", name_ar="اسبريسو")
        placed = MenuItem.objects.create(loyverse_id="old", pos_name="Old", display_name="My Name", price=1, category=cat,
                                         custom_section=section, image="https://example.com/mine.jpg")

        def item(i, name):
            return {"id": i, "item_name": name, "category_id": "c1",
                    "variants": [{"stores": [{"available_for_sale": True, "price": 2.5}]}]}

        pages = {
            ("categories", None): {"categories": [{"id": "c1", "name": "Coffee"}]},
            ("items", None): {"items": [item("old", "Old POS"), item("new1", "Latte")], "cursor": "p2"},
            ("items", "p2"): {"items": [item("new2", "Mocha")]},
        }

        def fake_get(url, headers=None, params=None, timeout=None):
            res = mock.Mock(status_code=200)
            res.json.return_value = pages[(url.rsplit("/", 1)[1], (params or {}).get("cursor"))]
            return res

        client = Client()
        client.post(reverse('accounts:staff_login_api'), {"username": "boss", "password": "boss-pass1"})
        with mock.patch("requests.get", side_effect=fake_get):
            response = client.post(reverse('accounts:staff_loyverse_sync_live'))
        self.assertEqual(response.status_code, 200)

        # Second page was read, and new items wait without a group
        self.assertEqual(set(MenuItem.objects.filter(custom_section__isnull=True).values_list("loyverse_id", flat=True)), {"new1", "new2"})
        # The item already placed keeps its group, name and image; only POS data changes
        placed.refresh_from_db()
        self.assertEqual((placed.custom_section, placed.display_name, placed.image, placed.pos_name),
                         (section, "My Name", "https://example.com/mine.jpg", "Old POS"))
        # Pending items are hidden from customers
        menu = Client().get(reverse('menu:client_menu')).content.decode()
        self.assertIn("My Name", menu)
        self.assertNotIn("Mocha", menu)

    def test_live_sync_reports_rejected_token(self):
        StaffUser.objects.create(username="boss", password_hash=make_password("boss-pass1"), employee_name="B", role="ADMIN")
        CafeConfiguration.objects.create(loyverse_api_token="bad")
        client = Client()
        client.post(reverse('accounts:staff_login_api'), {"username": "boss", "password": "boss-pass1"})
        with mock.patch("requests.get", return_value=mock.Mock(status_code=401)):
            response = client.post(reverse('accounts:staff_loyverse_sync_live'))
        self.assertEqual(response.status_code, 400)
        self.assertIn("غير صحيح", response.json()["message"])

    def test_admin_uploads_tab_and_group_images(self):
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from menu.models import MenuTab, MenuSection
        StaffUser.objects.create(username="boss", password_hash=make_password("boss-pass1"), employee_name="B", role="ADMIN")
        client = Client()
        client.post(reverse('accounts:staff_login_api'), {"username": "boss", "password": "boss-pass1"})
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            res = client.post(reverse('accounts:staff_tab_manage'), {"action": "create", "name_en": "Drinks", "name_ar": "مشروبات",
                              "image_file": SimpleUploadedFile("tab.png", png, content_type="image/png")})
            tab = MenuTab.objects.get(id=res.json()["tab_id"])
            self.assertTrue(tab.image.startswith("/media/menu/tabs/"))

            res = client.post(reverse('accounts:staff_section_manage'), {"action": "create", "tab_id": tab.id, "name_en": "Hot", "name_ar": "ساخن",
                              "banner_file": SimpleUploadedFile("g.png", png, content_type="image/png")})
            section = MenuSection.objects.get(id=res.json()["section_id"])
            self.assertTrue(section.banner_image.startswith("/media/menu/groups/"))

            # Saving without a new file keeps the current image
            client.post(reverse('accounts:staff_section_manage'), {"action": "update", "section_id": section.id, "name_en": "Hot2",
                        "name_ar": "ساخن", "banner_image": section.banner_image})
            section.refresh_from_db()
            self.assertTrue(section.banner_image.startswith("/media/menu/groups/"))

            bad = client.post(reverse('accounts:staff_tab_manage'), {"action": "create", "name_en": "X", "name_ar": "س",
                              "image_file": SimpleUploadedFile("x.txt", b"hi", content_type="text/plain")})
            self.assertEqual(bad.status_code, 400)

    def test_waiter_marks_item_sold_out_and_it_leaves_the_menu(self):
        from menu.models import MenuCategory, MenuItem, MenuTab, MenuSection
        cat = MenuCategory.objects.create(loyverse_category_id="c1", pos_category_name="Coffee", display_category_name="Coffee")
        section = MenuSection.objects.create(tab=MenuTab.objects.create(name_en="Hot", name_ar="ساخن"), name_en="Espresso", name_ar="اسبريسو")
        latte = MenuItem.objects.create(loyverse_id="a", pos_name="Latte", display_name="لاتيه", price=2, category=cat, custom_section=section)
        MenuItem.objects.create(loyverse_id="b", pos_name="Pending", display_name="صنف بالانتظار", price=2, category=cat)
        StaffUser.objects.create(username="w", password_hash=make_password("waiter-pass"), employee_name="W", role="WAITER")
        staff = Client()
        staff.post(reverse('accounts:staff_login_api'), {"username": "w", "password": "waiter-pass"})

        page = staff.get(reverse('accounts:staff_app')).content.decode()
        self.assertIn("لاتيه", page)
        self.assertIn("اسبريسو", page)
        self.assertNotIn("صنف بالانتظار", page)  # not on the menu, nothing to toggle

        staff.post(reverse('accounts:staff_menu_toggle_status'), {"item_id": latte.id, "status": "OUT_OF_STOCK"})
        self.assertNotIn("لاتيه", Client().get(reverse('menu:client_menu')).content.decode())
        staff.post(reverse('accounts:staff_menu_toggle_status'), {"item_id": latte.id, "status": "AVAILABLE"})
        self.assertIn("لاتيه", Client().get(reverse('menu:client_menu')).content.decode())

    def test_table_call_log_accept_arrive_and_dashboard(self):
        import json as _json
        from accounts.models import TableCall
        for name in ("sami", "rami"):
            StaffUser.objects.create(username=name, password_hash=make_password("waiter-pass"), employee_name=name.title(), role="WAITER")
        StaffUser.objects.create(username="boss", password_hash=make_password("boss-pass1"), employee_name="Boss", role="ADMIN")

        def login(name, pw="waiter-pass"):
            c = Client()
            c.post(reverse('accounts:staff_login_api'), {"username": name, "password": pw})
            return c

        def post(client, url, **body):
            return client.post(reverse(url), data=_json.dumps(body), content_type="application/json")

        sami, rami, boss = login("sami"), login("rami"), login("boss", "boss-pass1")
        call = TableCall.objects.create(table_number="7", call_type="call")
        bill = TableCall.objects.create(table_number="7", call_type="bill")

        # Not logged in: refused
        self.assertEqual(post(Client(), 'accounts:staff_table_calls_accept', call_id=call.id).status_code, 401)
        # Open calls show up when the staff app loads
        self.assertIn(f'"id": {call.id}', sami.get(reverse('accounts:staff_app')).content.decode())

        self.assertEqual(post(sami, 'accounts:staff_table_calls_accept', call_id=call.id).status_code, 200)
        # Second waiter loses, and is told who took it
        lost = post(rami, 'accounts:staff_table_calls_accept', call_id=call.id)
        self.assertEqual(lost.status_code, 409)
        self.assertIn("Sami", lost.json()["message"])
        # The bill from the same table went with the same visit
        bill.refresh_from_db()
        self.assertEqual(bill.accepted_by.username, "sami")

        # Only the waiter who took it can confirm arrival
        self.assertEqual(post(rami, 'accounts:staff_table_calls_arrived', call_id=call.id).status_code, 404)
        self.assertEqual(post(sami, 'accounts:staff_table_calls_arrived', call_id=call.id).status_code, 200)
        call.refresh_from_db()
        self.assertIsNotNone(call.arrived_at)

        self.assertEqual(sami.get(reverse('accounts:staff_table_calls_log')).status_code, 401)
        log = boss.get(reverse('accounts:staff_table_calls_log')).json()
        self.assertEqual(len(log["calls"]), 2)
        self.assertEqual(log["waiters"][0]["waiter"], "Sami")
        self.assertEqual(log["waiters"][0]["calls"], 2)
        self.assertEqual(log["waiters"][0]["not_confirmed"], 0)

    def test_arrival_needs_table_qr_when_enabled(self):
        import json as _json
        from django.utils import timezone
        from accounts.models import TableCall
        sami = StaffUser.objects.create(username="sami", password_hash=make_password("waiter-pass"), employee_name="Sami", role="WAITER")
        CafeConfiguration.objects.create(arrival_qr_required=True)
        call = TableCall.objects.create(table_number="7", call_type="call", accepted_by=sami, accepted_at=timezone.now())
        client = Client()
        client.post(reverse('accounts:staff_login_api'), {"username": "sami", "password": "waiter-pass"})

        def arrive(scanned):
            return client.post(reverse('accounts:staff_table_calls_arrived'), content_type="application/json",
                               data=_json.dumps({"call_id": call.id, "scanned": scanned}))

        self.assertEqual(arrive("").status_code, 400)
        self.assertEqual(arrive("https://whitebirdcafe.com/table/3/?auth=x").status_code, 400)
        self.assertEqual(arrive("https://whitebirdcafe.com/table/7/?auth=x").status_code, 200)
        call.refresh_from_db()
        self.assertIsNotNone(call.arrived_at)

    def test_shifts_only_from_cafe_wifi(self):
        import json as _json
        StaffUser.objects.create(username="sami", password_hash=make_password("waiter-pass"), employee_name="Sami", role="WAITER")
        StaffUser.objects.create(username="boss", password_hash=make_password("boss-pass1"), employee_name="Boss", role="ADMIN")
        cafe, home = "92.253.10.10", "176.29.5.7"

        def login(name, pw, ip):
            c = Client(HTTP_X_FORWARDED_FOR=ip)
            c.post(reverse('accounts:staff_login_api'), {"username": name, "password": pw})
            return c

        def shift(client, action, **extra):
            return client.post(reverse('accounts:staff_shift'), data=_json.dumps({"action": action}),
                               content_type="application/json", **extra)

        boss = login("boss", "boss-pass1", f"10.0.0.5, {cafe}, 10.1.2.3")
        self.assertEqual(boss.post(reverse('accounts:staff_cafe_network'), data="{}", content_type="application/json")
                         .json()["ips"], [cafe])

        sami = login("sami", "waiter-pass", cafe)
        # From home, or faking the cafe address at the front of the header: refused
        self.assertEqual(shift(sami, "start", HTTP_X_FORWARDED_FOR=home).status_code, 403)
        self.assertEqual(shift(sami, "start", HTTP_X_FORWARDED_FOR=f"{cafe}, {home}").status_code, 403)

        self.assertEqual(shift(sami, "start", HTTP_X_FORWARDED_FOR=cafe).status_code, 200)
        self.assertEqual(shift(sami, "start", HTTP_X_FORWARDED_FOR=cafe).status_code, 400)  # already on shift
        log = boss.get(reverse('accounts:staff_shifts_log')).json()
        self.assertEqual(log["on_shift"], ["Sami"])
        self.assertEqual(shift(sami, "end", HTTP_X_FORWARDED_FOR=cafe).status_code, 200)
        log = boss.get(reverse('accounts:staff_shifts_log')).json()
        self.assertEqual(log["on_shift"], [])
        self.assertEqual(log["totals"][0]["staff"], "Sami")
        self.assertEqual(sami.get(reverse('accounts:staff_shifts_log')).status_code, 401)


class StaffPwaTests(TestCase):
    def test_manifest_and_service_worker(self):
        import json, pathlib
        from django.conf import settings
        manifest = json.loads((pathlib.Path(settings.BASE_DIR) / "static" / "manifest.json").read_text())
        self.assertEqual(manifest["start_url"], "/staff/app/")
        self.assertEqual(manifest["scope"], "/staff/")
        res = self.client.get("/staff/sw.js")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/javascript")
        self.assertIn(b"addEventListener('push'", res.content)
        login = self.client.get("/staff/login/")
        self.assertContains(login, 'rel="manifest"')
        self.assertContains(login, "installStaffApp()")

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
        self.assertIn("18 or older", response.json()["message"])

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
        self.assertEqual(RaffleTicket.objects.filter(customer=self.customer).count(), 1)  # one receipt = one draw entry

        # Second submission with same receipt_number - no double crediting
        res2 = client.post(self.webhook_url(), data=payload, content_type='application/json')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json()["results"][0]["status"], "duplicate")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 20)  # Points remain 20, not 40!
        self.assertEqual(RaffleTicket.objects.filter(customer=self.customer).count(), 1)  # one receipt = one draw entry

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
        self.assertContains(login, "togglePassword(this)")
        self.assertNotContains(login, "ADMIN_RESET_PASSWORD")


class WhatsAppCloudTests(TestCase):
    def setUp(self):
        cache.clear()
        self.config = CafeConfiguration.objects.create(
            wa_phone_number_id="111", wa_business_account_id="222",
            wa_access_token="tok", wa_verify_token="verify-me")

    def fake_graph(self, calls):
        def request(method, url, headers=None, json=None, params=None, timeout=None):
            calls.append({"method": method, "url": url, "json": json, "auth": headers["Authorization"]})
            return mock.Mock(status_code=200, json=lambda: {"messages": [{"id": "wamid.1"}]})
        return request

    def test_template_send_and_delivery_status(self):
        from accounts.models import WhatsAppMessage
        from cafe_smart.integrations import WhatsAppClient
        calls = []
        with mock.patch("requests.request", side_effect=self.fake_graph(calls)):
            self.assertTrue(WhatsAppClient.send_otp("0791112233", "123456"))
        body = calls[0]["json"]
        self.assertTrue(calls[0]["url"].endswith("/111/messages"))
        self.assertEqual(calls[0]["auth"], "Bearer tok")
        self.assertEqual(body["to"], "962791112233")
        self.assertEqual(body["template"]["name"], "wb_verification_code")
        self.assertEqual(body["template"]["components"][1]["parameters"][0]["text"], "123456")
        log = WhatsAppMessage.objects.get()
        self.assertEqual((log.status, log.wa_message_id), ("sent", "wamid.1"))
        self.assertNotIn("123456", log.text)

        status = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "delivered"}]}}]}]}
        self.client.post("/api/whatsapp/webhook/", data=status, content_type="application/json")
        log.refresh_from_db()
        self.assertEqual(log.status, "delivered")

    def test_webhook_verify_and_incoming_message(self):
        from accounts.models import ContactMessage
        url = "/api/whatsapp/webhook/"
        ok = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
        self.assertEqual(ok.content, b"42")
        self.assertEqual(self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code, 403)

        incoming = {"entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "962791112233", "profile": {"name": "Sara"}}],
            "messages": [{"from": "962791112233", "id": "wamid.in", "type": "text", "text": {"body": "مرحبا"}}]}}]}]}
        self.client.post(url, data=incoming, content_type="application/json")
        self.client.post(url, data=incoming, content_type="application/json")  # Meta retries: stored once
        msg = ContactMessage.objects.get()
        self.assertEqual((msg.name, msg.contact, msg.message), ("Sara", "+962791112233", "مرحبا"))

        # With an app secret set, unsigned calls are refused
        self.config.wa_app_secret = "s3cret"
        self.config.save()
        self.assertEqual(self.client.post(url, data=incoming, content_type="application/json").status_code, 403)

    def test_staff_password_reset_by_whatsapp(self):
        from django.contrib.auth.hashers import check_password
        staff = StaffUser.objects.create(username="Boss", employee_name="Boss", role="ADMIN",
                                         phone_number="+962791234567", password_hash=make_password("old-password"))
        sent = {}
        with mock.patch("cafe_smart.integrations.WhatsAppClient.send_otp",
                        side_effect=lambda phone, code: sent.update(phone=phone, code=code) or True):
            res = self.client.post("/api/staff/forgot-password/", {"username": "boss"})
            nobody = self.client.post("/api/staff/forgot-password/", {"username": "nobody"})
        self.assertEqual(res.json()["message"], nobody.json()["message"])
        self.assertEqual(sent["phone"], "+962791234567")

        wrong = self.client.post("/api/staff/forgot-password/reset/", {"otp": "000000", "new_password": "new-password"})
        self.assertEqual(wrong.status_code, 400)
        ok = self.client.post("/api/staff/forgot-password/reset/", {"otp": sent["code"], "new_password": "new-password"})
        self.assertEqual(ok.status_code, 200, ok.content)
        staff.refresh_from_db()
        self.assertTrue(check_password("new-password", staff.password_hash))

    def test_saving_settings_keeps_secret_token(self):
        admin = StaffUser.objects.create(username="admin1", employee_name="A", role="ADMIN",
                                         password_hash=make_password("admin-pass-1"))
        session = self.client.session
        session["staff_user_id"] = admin.id
        session["staff_role"] = "ADMIN"
        session.save()
        self.client.post("/api/staff/config/edit/", {"wa_phone_number_id": "999", "wa_access_token": ""})
        self.config.refresh_from_db()
        self.assertEqual((self.config.wa_phone_number_id, self.config.wa_access_token), ("999", "tok"))


class CafeLocationTests(TestCase):
    def setUp(self):
        self.config = CafeConfiguration.objects.create()
        session = self.client.session
        session["staff_user_id"] = 1
        session["staff_role"] = "ADMIN"
        session.save()

    def test_coordinates_from_full_link(self):
        from accounts.views import coordinates_from_maps_link
        link = "https://www.google.com/maps/place/White+Bird/@30.19,35.73,17z/data=!3m1!4b1!4m6!3m5!8m2!3d30.19625!4d35.73412"
        self.assertEqual(coordinates_from_maps_link(link), (30.19625, 35.73412))

    def test_short_link_is_followed_and_saved(self):
        redirect = mock.Mock(headers={"Location": "https://www.google.com/maps/place/x/@30.2,35.7,17z/data=!3d30.19625!4d35.73412"}, text="")
        with mock.patch("requests.get", return_value=redirect) as get:
            res = self.client.post("/api/staff/config/edit/", {"maps_link": "https://maps.app.goo.gl/AbC123"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get.call_args[0][0], "https://maps.app.goo.gl/AbC123")
        self.config.refresh_from_db()
        self.assertEqual((self.config.latitude, self.config.longitude), (30.19625, 35.73412))

    def test_iphone_short_link_reads_coordinates_from_page(self):
        from accounts.views import coordinates_from_maps_link
        hops = [
            mock.Mock(status_code=302, headers={"Location": "https://maps.google.com/?q=White+Bird+Cafe,+Ma'an&ftid=0x1:0x2&entry=gps"}, text=""),
            mock.Mock(status_code=302, headers={"Location": "https://consent.google.com/ml?continue=https://www.google.com/maps?q%3DWhite%2BBird%26ftid%3D0x1:0x2"}, text=""),
            mock.Mock(status_code=200, headers={}, text='<meta content="https://maps.google.com/maps/api/staticmap?center=30.19625%2C35.73412&amp;zoom=16">'),
        ]
        with mock.patch("requests.get", side_effect=hops) as get:
            self.assertEqual(coordinates_from_maps_link("https://maps.app.goo.gl/XT6REDn6q?g_st=ic"), (30.19625, 35.73412))
        self.assertEqual(get.call_args[0][0], "https://www.google.com/maps?q=White+Bird&ftid=0x1:0x2")

    def test_coordinates_from_page_start_up_data(self):
        from accounts.views import _coordinates_in
        self.assertEqual(_coordinates_in("window.APP_INITIALIZATION_STATE=[[[2105.3,35.73412,30.19625],[0,0,0]"), (30.19625, 35.73412))

    def test_non_google_link_is_refused(self):
        with mock.patch("requests.get") as get:
            res = self.client.post("/api/staff/config/edit/", {"maps_link": "https://evil.example.com/@1.0,2.0"})
        self.assertEqual(res.status_code, 400)
        with mock.patch("requests.get") as get:
            res = self.client.post("/api/staff/config/edit/", {"maps_link": "http://169.254.169.254/latest"})
        get.assert_not_called()
        self.assertEqual(res.status_code, 400)
        self.config.refresh_from_db()
        self.assertEqual(self.config.latitude, CafeConfiguration().latitude)


class StaffAppBatchTests(TestCase):
    def setUp(self):
        cache.clear()
        from django.contrib.auth.hashers import make_password
        self.config = CafeConfiguration.objects.create(cafe_network_ips="92.253.10.10")
        self.waiter = StaffUser.objects.create(username="w", employee_name="Sami", role="WAITER", password_hash=make_password("x"))

    def login(self, user):
        session = self.client.session
        session["staff_user_id"] = user.id
        session["staff_role"] = user.role
        session.save()

    def test_only_trusted_staff_approve_network_and_admin_can_undo(self):
        self.login(self.waiter)
        res = self.client.post("/api/staff/shift/", '{"action": "start"}', content_type="application/json", REMOTE_ADDR="176.29.5.7")
        self.assertEqual(res.json()["code"], "not_cafe_network")
        self.assertFalse(res.json()["can_approve"])
        self.assertEqual(self.client.post("/api/staff/cafe-network/", '{"action": "use"}', content_type="application/json", REMOTE_ADDR="176.29.5.7").status_code, 401)

        self.waiter.can_approve_network = True
        self.waiter.save()
        with mock.patch("accounts.push_notifications.send_web_push_to_staff") as push:
            res = self.client.post("/api/staff/cafe-network/", '{"action": "use"}', content_type="application/json", REMOTE_ADDR="176.29.5.7")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(push.call_args.kwargs["admins_only"])
        self.config.refresh_from_db()
        self.assertEqual(self.config.cafe_network_ips.splitlines(), ["92.253.10.10", "176.29.5.7"])
        self.assertEqual(self.config.cafe_network_changed_by, "Sami")
        self.assertEqual(self.client.post("/api/staff/cafe-network/", '{"action": "undo"}', content_type="application/json").status_code, 401)

        admin = StaffUser.objects.create(username="a", employee_name="Boss", role="ADMIN", password_hash="x")
        self.login(admin)
        self.client.post("/api/staff/cafe-network/", '{"action": "undo"}', content_type="application/json")
        self.config.refresh_from_db()
        self.assertEqual(self.config.cafe_network_ips, "92.253.10.10")

    def test_contact_message_sends_phone_notification(self):
        with mock.patch("accounts.push_notifications.send_web_push_to_staff") as push:
            res = self.client.post("/contact/submit/", {"name": "Lina", "contact": "0790000000", "message": "Hi"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Lina", push.call_args.args[0])

    def test_push_unsubscribe(self):
        from accounts.models import StaffPushSubscription
        StaffPushSubscription.objects.create(staff_user=self.waiter, endpoint="https://push/1", p256dh="k", auth="a")
        self.login(self.waiter)
        self.client.post("/api/staff/push-unsubscribe/", '{"endpoint": "https://push/1"}', content_type="application/json")
        self.assertFalse(StaffPushSubscription.objects.exists())


class TableCallSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.config = CafeConfiguration.objects.create(cafe_network_ips="92.253.10.10", wifi_ssid="WhiteBird")
        self.admin = StaffUser.objects.create(username="a", employee_name="Boss", role="ADMIN", password_hash="x")
        self.hookah = StaffUser.objects.create(username="h", employee_name="Ali", role="WAITER", password_hash="x")

    def login(self, user):
        session = self.client.session
        session["staff_user_id"] = user.id
        session["staff_role"] = user.role
        session.save()

    def test_table_page_needs_code_and_cafe_wifi(self):
        from accounts.security import table_token
        token = table_token(self.config, 3)
        ok = self.client.get(f"/table/3/?auth={token}", REMOTE_ADDR="92.253.10.10")
        self.assertEqual(ok.context["table_status"], "ok")
        self.assertEqual(ok.context["auth_token"], token)
        self.assertEqual(self.client.get("/table/3/?auth=wrong", REMOTE_ADDR="92.253.10.10").context["table_status"], "no_code")
        self.assertEqual(self.client.get(f"/table/4/?auth={token}", REMOTE_ADDR="92.253.10.10").context["table_status"], "no_code")
        outside = self.client.get(f"/table/3/?auth={token}", REMOTE_ADDR="176.29.5.7")
        self.assertEqual(outside.context["table_status"], "no_wifi")
        self.assertEqual(outside.context["auth_token"], "")
        self.assertContains(outside, "WhiteBird")

        self.config.table_calls_wifi_only = False
        self.config.save()
        self.assertEqual(self.client.get(f"/table/3/?auth={token}", REMOTE_ADDR="176.29.5.7").context["table_status"], "ok")

    def test_qr_sheet_is_for_manager_and_codes_can_be_changed(self):
        from accounts.security import table_token
        self.assertEqual(self.client.get("/staff/tables/qr/").status_code, 302)
        self.login(self.hookah)
        self.assertEqual(self.client.get("/staff/tables/qr/").status_code, 302)
        self.assertEqual(self.client.post("/api/staff/tables/rotate-code/").status_code, 401)

        self.login(self.admin)
        sheet = self.client.get("/staff/tables/qr/")
        self.assertEqual(sheet.context["total"], 20)
        old = table_token(self.config, 1)
        self.assertContains(sheet, f"/table/1/?auth={old}")
        self.assertEqual(self.client.post("/api/staff/tables/rotate-code/").status_code, 200)
        self.config.refresh_from_db()
        self.assertNotEqual(table_token(self.config, 1), old)

    def test_hookah_calls_ring_the_hookah_person(self):
        from asgiref.sync import async_to_sync
        from accounts.consumers import trigger_staff_web_push
        self.config.hookah_staff = self.hookah
        self.config.save()
        with mock.patch("accounts.push_notifications.send_web_push_to_staff") as push:
            async_to_sync(trigger_staff_web_push)("5", "hookah")
        self.assertEqual(push.call_args.kwargs["staff_ids"], [self.hookah.id])
        self.assertTrue(push.call_args.kwargs["include_admins"])
        with mock.patch("accounts.push_notifications.send_web_push_to_staff") as push:
            async_to_sync(trigger_staff_web_push)("5", "waiter")
        self.assertNotIn("staff_ids", push.call_args.kwargs)

    def test_settings_save_table_options(self):
        self.login(self.admin)
        self.client.post("/api/staff/config/edit/", {"table_count": "12", "hookah_staff": str(self.hookah.id)})
        self.config.refresh_from_db()
        self.assertEqual(self.config.table_count, 12)
        self.assertEqual(self.config.hookah_staff, self.hookah)
        self.assertFalse(self.config.table_calls_wifi_only)


class DashboardMembersTests(TestCase):
    def test_dashboard_lists_members_with_tier_and_stats(self):
        from accounts.models import PointsTransaction
        config = CafeConfiguration.objects.create()
        admin = StaffUser.objects.create(username="a", employee_name="Boss", role="ADMIN", password_hash="x")
        session = self.client.session
        session["staff_user_id"] = admin.id
        session["staff_role"] = "ADMIN"
        session.save()
        rich = CustomerProfile.objects.create(loyverse_customer_id="c1", phone_number="+962790000001", password_hash="x",
                                              full_name="Lina", birth_date=datetime.date(1990, 1, 1), points_balance=50)
        CustomerProfile.objects.create(loyverse_customer_id="c2", phone_number="+962790000002", password_hash="x",
                                       full_name="Omar", birth_date=datetime.date(1990, 1, 1))
        PointsTransaction.objects.create(customer=rich, points=config.gold_tier_threshold, transaction_type="EARNED")
        res = self.client.get("/staff/dashboard/")
        self.assertEqual(res.context["stats"]["members"], 2)
        tiers = {row["customer"].full_name: row["tier"] for row in res.context["customers"]}
        self.assertEqual(tiers, {"Lina": "Gold", "Omar": "Bronze"})
        self.assertContains(res, "ذهبي")
        self.assertNotContains(res, "Cafe Settings")


class MonthlyDrawTests(TestCase):
    def setUp(self):
        cache.clear()
        self.config = CafeConfiguration.objects.create()
        self.admin = StaffUser.objects.create(username="a", employee_name="Boss", role="ADMIN", password_hash="x")
        session = self.client.session
        session["staff_user_id"] = self.admin.id
        session["staff_role"] = "ADMIN"
        session.save()
        self.members = [CustomerProfile.objects.create(
            loyverse_customer_id=f"c{i}", phone_number=f"+96279000000{i}", password_hash="x",
            full_name=f"M{i}", birth_date=datetime.date(1990, 1, 1)) for i in range(5)]

    def ticket(self, customer, n, when=None):
        t = RaffleTicket.objects.create(ticket_code=f"TKT-{n}", customer=customer, receipt_number=f"R-{n}", receipt_amount=3)
        if when:
            RaffleTicket.objects.filter(pk=t.pk).update(created_at=when)
        return t

    def test_three_different_members_win_and_month_closes(self):
        from django.utils import timezone
        for i in range(12):
            self.ticket(self.members[i % 4], i)
        old = self.ticket(self.members[4], 99, timezone.now() - datetime.timedelta(days=62))
        self.client.post("/api/staff/raffle/prizes/", {"prize1_name": "بوكس قهوة", "prize2_name": "كيكة", "prize3_name": "مشروب"})
        info = self.client.get("/api/staff/raffle/month/").json()["draw"]
        self.assertEqual((info["entries"], info["members"]), (12, 4))
        self.assertEqual(info["first"]["receipt"], "R-0")
        with mock.patch("cafe_smart.integrations.WhatsAppClient.send_winner_notification") as send:
            res = self.client.post("/api/staff/raffle/draw/")
        self.assertEqual(res.status_code, 200)
        winners = res.json()["draw"]["winners"]
        self.assertEqual([w["place"] for w in winners], [1, 2, 3])
        self.assertEqual(len({w["phone"] for w in winners}), 3)
        self.assertEqual(send.call_count, 3)
        self.assertIn("بوكس قهوة", send.call_args_list[0].args[3])
        self.assertEqual(RaffleTicket.objects.filter(draw_status="PENDING").exclude(pk=old.pk).count(), 0)
        old.refresh_from_db()
        self.assertEqual(old.draw_status, "PENDING")  # other months are untouched
        self.assertEqual(self.client.post("/api/staff/raffle/draw/").status_code, 400)  # no second draw

    def test_receipt_gives_one_entry_without_minimum(self):
        from accounts.views import _process_loyverse_receipt
        member = self.members[0]
        result = _process_loyverse_receipt({"receipt_number": "R-1", "customer_id": member.loyverse_customer_id, "total_money": 1.5, "points_earned": 1})
        self.assertEqual(result["tickets_generated"], 1)

    def test_loyalty_page_shows_month_prizes_and_entries(self):
        self.client.post("/api/staff/raffle/prizes/", {"prize1_name": "بوكس قهوة"})
        self.ticket(self.members[0], 1)
        session = self.client.session
        session["customer_id"] = self.members[0].id
        session.save()
        with mock.patch("cafe_smart.integrations.LoyverseClient.refresh_customer_points", return_value=False):
            page = self.client.get("/loyalty/")
        self.assertContains(page, "بوكس قهوة")
        self.assertEqual(page.context["my_entries"], 1)
        self.assertNotContains(page, "October")


class BirthdayAndOfferTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.today = timezone.localdate()
        self.config = CafeConfiguration.objects.create(birthday_messages_enabled=True)
        self.yes = CustomerProfile.objects.create(loyverse_customer_id="c1", phone_number="+962790000001", password_hash="x",
                                                  full_name="Lina", birth_date=self.today.replace(year=1990), marketing_opt_in=True)
        self.no = CustomerProfile.objects.create(loyverse_customer_id="c2", phone_number="+962790000002", password_hash="x",
                                                 full_name="Omar", birth_date=self.today.replace(year=1991))

    def test_birthday_gift_goes_once_to_members_who_agreed(self):
        from accounts.birthdays import send_birthday_messages
        with mock.patch("cafe_smart.integrations.WhatsAppClient.send_birthday_gift", return_value=True) as send:
            self.assertEqual(send_birthday_messages(self.today), 1)
            self.assertEqual(send_birthday_messages(self.today), 0)
        self.assertEqual(send.call_args.args[0], "+962790000001")
        self.assertEqual(send.call_args.args[2], self.config.birthday_msg_bronze)

    def test_daily_claim_happens_once(self):
        from accounts.birthdays import claim_today
        self.assertTrue(claim_today(self.config.id, self.today))
        self.assertFalse(claim_today(self.config.id, self.today))

    def test_offer_goes_only_to_members_who_agreed(self):
        admin = StaffUser.objects.create(username="a", employee_name="Boss", role="ADMIN", password_hash="x")
        session = self.client.session
        session["staff_user_id"] = admin.id
        session["staff_role"] = "ADMIN"
        session.save()
        self.assertContains(self.client.get("/staff/offers/"), "كل الموافقين (1)")

        class Now:
            def __init__(self, target, daemon=None, **kw):
                self.target = target
            def start(self):
                self.target()
        with mock.patch("threading.Thread", Now), \
             mock.patch("cafe_smart.integrations.WhatsAppClient.send_offer", return_value=True) as send:
            res = self.client.post("/api/staff/offers/send/", '{"text": "خصم 20% على البارد", "tier": ""}', content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([c.args[0] for c in send.call_args_list], ["+962790000001"])

    def test_member_can_turn_messages_off(self):
        session = self.client.session
        session["customer_id"] = self.yes.id
        session.save()
        self.client.post("/api/customer/marketing-opt-in/", {"opt_in": "0"})
        self.yes.refresh_from_db()
        self.assertFalse(self.yes.marketing_opt_in)



class TableHallsTests(TestCase):
    def setUp(self):
        self.config = CafeConfiguration.objects.create(table_count=3)
        session = self.client.session
        session["staff_user_id"] = 1
        session["staff_role"] = "ADMIN"
        session.save()

    def test_without_halls_tables_are_numbers(self):
        self.assertEqual(self.config.table_groups()[0]["tables"], ["1", "2", "3"])

    def test_halls_are_saved_and_printed_with_their_codes(self):
        import json
        halls = [{"code": "f", "name": "الصالة الأمامية", "start": 1, "count": 2},
                 {"code": "V", "name": "VIP", "start": 10, "count": 2}]
        res = self.client.post("/api/staff/config/edit/", {"table_count": "3", "table_halls": json.dumps(halls)})
        self.assertEqual(res.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual([g["tables"] for g in self.config.table_groups()], [["F1", "F2"], ["V10", "V11"]])
        page = self.client.get("/staff/tables/qr/").content.decode()
        self.assertIn("/table/F2/?auth=", page)
        self.assertIn("/table/V11/?auth=", page)
        self.assertIn("الصالة الأمامية", page)

    def test_bad_hall_code_is_refused(self):
        import json
        for halls in ([{"code": "ف", "name": "x", "count": 3}], [{"code": "F", "count": 2}, {"code": "F", "count": 1}]):
            res = self.client.post("/api/staff/config/edit/", {"table_count": "3", "table_halls": json.dumps(halls)})
            self.assertEqual(res.status_code, 400)
        self.config.refresh_from_db()
        self.assertEqual(self.config.table_halls, [])

    def test_table_page_opens_for_a_hall_table(self):
        from accounts.security import table_token
        self.config.table_halls = [{"code": "F", "name": "", "start": 1, "count": 20}]
        self.config.table_calls_wifi_only = False
        self.config.save()
        res = self.client.get(f"/table/F15/?auth={table_token(self.config, 'F15')}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("F15", res.content.decode())



class TableCardTemplateTests(TestCase):
    def setUp(self):
        import tempfile
        self.media = self.settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.media.enable()
        self.config = CafeConfiguration.objects.create(table_count=2)
        session = self.client.session
        session["staff_user_id"] = 1
        session["staff_role"] = "ADMIN"
        session.save()

    def tearDown(self):
        self.media.disable()

    def png(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = io.BytesIO()
        Image.new("RGB", (20, 30), (255, 255, 255)).save(buf, "PNG")
        return SimpleUploadedFile("card.png", buf.getvalue(), content_type="image/png")

    def test_template_and_layout_are_saved_and_clamped(self):
        import json
        layout = {"use": True, "width_cm": 99, "qr": {"x": 40, "y": 60, "size": 30},
                  "num": {"x": 50, "y": 85, "w": 30, "h": 8, "color": "red", "bg": "#112233", "prefix": "Table"}}
        res = self.client.post("/api/staff/tables/card-template/", {"template": self.png(), "layout": json.dumps(layout)})
        self.assertEqual(res.status_code, 200)
        self.config.refresh_from_db()
        self.assertTrue(self.config.table_card_template)
        saved = self.config.table_card_layout
        self.assertEqual(saved["width_cm"], 30)
        self.assertEqual((saved["qr"]["x"], saved["qr"]["size"]), (40, 30))
        self.assertEqual((saved["num"]["color"], saved["num"]["bg"], saved["num"]["w"]), ("#000000", "#112233", 30))
        page = self.client.get("/staff/tables/qr/").content.decode()
        self.assertIn(self.config.table_card_template.url, page)

    def test_non_image_is_refused_and_staff_need_admin(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        res = self.client.post("/api/staff/tables/card-template/", {"template": SimpleUploadedFile("x.pdf", b"%PDF", content_type="application/pdf")})
        self.assertEqual(res.status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.post("/api/staff/tables/card-template/", {"remove": "1"}).status_code, 401)

    def test_remove_turns_template_printing_off(self):
        import json
        self.client.post("/api/staff/tables/card-template/", {"template": self.png(), "layout": json.dumps({"use": True})})
        res = self.client.post("/api/staff/tables/card-template/", {"remove": "1"})
        self.assertFalse(res.json()["layout"]["use"])
        self.config.refresh_from_db()
        self.assertFalse(self.config.table_card_template)

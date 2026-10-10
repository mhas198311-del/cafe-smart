import datetime
import jwt
from django.test import TestCase, Client
from django.conf import settings
from django.urls import reverse
from accounts.models import CustomerProfile, StaffUser

class AccountsTests(TestCase):
    def setUp(self):
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

    def test_loyverse_webhook_idempotency(self):
        """
        Verifies that duplicate webhook events with the same receipt_number are handled idempotently.
        """
        client = Client()
        payload = {
            "event_type": "receipt.created",
            "data": {
                "customer_id": self.customer.loyverse_customer_id,
                "receipt_number": "LV-TEST-IDEMPOTENT-001",
                "total_amount": 20.0,
                "points_earned": 20
            }
        }
        
        # First submission - should credit points
        res1 = client.post(reverse('accounts:loyverse_webhook'), data=payload, content_type='application/json')
        self.assertEqual(res1.status_code, 200)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 20)
        
        # Second submission with same receipt_number - should return success without double crediting
        res2 = client.post(reverse('accounts:loyverse_webhook'), data=payload, content_type='application/json')
        self.assertEqual(res2.status_code, 200)
        self.assertIn("Idempotent", res2.json()["message"])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.points_balance, 20)  # Points remain 20, not 40!


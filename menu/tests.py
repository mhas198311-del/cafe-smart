import json
from django.test import TestCase, Client
from django.urls import reverse
from menu.models import MenuCategory, MenuItem

class MenuTests(TestCase):
    def setUp(self):
        # Create default category and items
        self.category = MenuCategory.objects.create(
            loyverse_category_id="cat-001",
            pos_category_name="Beverages",
            display_category_name="Premium Beverages"
        )
        self.item_avail = MenuItem.objects.create(
            loyverse_id="item-001",
            pos_name="Espresso",
            display_name="Double Espresso Shot",
            price=2.500,
            category=self.category,
            status="AVAILABLE",
            loyverse_stock=5
        )
        self.item_out = MenuItem.objects.create(
            loyverse_id="item-002",
            pos_name="Latte",
            display_name="Golden Latte",
            price=3.500,
            category=self.category,
            status="OUT_OF_STOCK",
            loyverse_stock=10
        )
        self.item_auto_in = MenuItem.objects.create(
            loyverse_id="item-003",
            pos_name="Cappuccino",
            display_name="Fluffy Cappuccino",
            price=3.000,
            category=self.category,
            status="AUTOMATIC",
            loyverse_stock=2
        )
        self.item_auto_out = MenuItem.objects.create(
            loyverse_id="item-004",
            pos_name="Mocha",
            display_name="White Chocolate Mocha",
            price=4.000,
            category=self.category,
            status="AUTOMATIC",
            loyverse_stock=0
        )

    def test_client_menu_view_filtering(self):
        """
        Verifies that out of stock items are hidden,
        and automatic items with stock <= 0 are hidden.
        """
        client = Client()
        response = client.get(reverse('menu:client_menu'))
        self.assertEqual(response.status_code, 200)
        
        # Check rendered context items
        tabs_data = response.context['tabs_data']
        self.assertEqual(len(tabs_data), 1)  # Only General tab
        
        items = tabs_data[0]['sections'][0]['items']
        # Double Espresso (AVAILABLE) and Fluffy Cappuccino (AUTOMATIC, stock 2) should be present
        # Golden Latte (OUT_OF_STOCK) and Mocha (AUTOMATIC, stock 0) should be excluded
        item_ids = [item.loyverse_id for item in items]
        self.assertIn("item-001", item_ids)
        self.assertIn("item-003", item_ids)
        self.assertNotIn("item-002", item_ids)
        self.assertNotIn("item-004", item_ids)

    def test_loyverse_webhook_sync(self):
        """
        Tests webhook sync creating/updating items.
        """
        client = Client()
        webhook_data = {
            "event_type": "items.update",
            "items": [
                {
                    "id": "item-005",
                    "name": "Flat White",
                    "category_id": "cat-001",
                    "price": 3.200,
                    "stock": 15
                }
            ]
        }
        response = client.post(
            reverse('menu:loyverse_webhook'),
            data=json.dumps(webhook_data),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        
        # Check if item created
        flat_white = MenuItem.objects.get(loyverse_id="item-005")
        self.assertEqual(flat_white.pos_name, "Flat White")
        self.assertEqual(float(flat_white.price), 3.200)
        self.assertEqual(flat_white.loyverse_stock, 15)

    def test_contact_submit_success(self):
        """
        Tests successful contact form submission saves ContactMessage in the database.
        """
        client = Client()
        response = client.post(
            reverse('menu:contact_submit'),
            data={
                "name": "Jane Doe",
                "contact": "jane@example.com",
                "type": "General Inquiry",
                "message": "Hello, I love your coffee!"
            }
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["status"], "success")

        # Verify DB entry
        from accounts.models import ContactMessage
        msg = ContactMessage.objects.get(name="Jane Doe")
        self.assertEqual(msg.contact, "jane@example.com")
        self.assertEqual(msg.message, "Hello, I love your coffee!")


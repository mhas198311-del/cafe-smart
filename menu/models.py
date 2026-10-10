from django.db import models

class MenuCategory(models.Model):
    loyverse_category_id = models.CharField(max_length=255, unique=True, help_text="Direct link to Loyverse Category ID")
    pos_category_name = models.CharField(max_length=255, help_text="Original practical name in Loyverse POS")
    display_category_name = models.CharField(max_length=255, help_text="Premium marketing name displayed on web")
    category_image = models.URLField(blank=True, null=True)
    order_index = models.IntegerField(default=0, help_text="Custom presentation ordering")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.display_category_name

class MenuTab(models.Model):
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    image = models.URLField(blank=True, null=True, help_text="Image URL for this tab that changes when selected")
    order_index = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name_en} / {self.name_ar}"

class MenuSection(models.Model):
    tab = models.ForeignKey(MenuTab, on_delete=models.CASCADE, related_name="sections")
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    banner_image = models.URLField(blank=True, null=True, help_text="Wide high-fidelity banner image URL")
    order_index = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name_en} / {self.name_ar} (Tab: {self.tab.name_en})"

class MenuItem(models.Model):
    STATUS_CHOICES = [
        ('AVAILABLE', 'Force Available (Ignore Negative Stock)'),
        ('OUT_OF_STOCK', 'Force Hidden / Temporary Out of Stock'),
        ('AUTOMATIC', 'Follow Loyverse Live Stock tracking')
    ]
    loyverse_id = models.CharField(max_length=255, unique=True, help_text="Loyverse Item ID")
    pos_name = models.CharField(max_length=255, help_text="Original short name in POS")
    display_name = models.CharField(max_length=255, help_text="Premium display name for customers")
    description = models.TextField(blank=True, null=True, help_text="Marketing food/drink description")
    price = models.DecimalField(max_digits=10, decimal_places=3)
    image = models.URLField(blank=True, null=True)
    category = models.ForeignKey(MenuCategory, on_delete=models.CASCADE, related_name="items")
    custom_section = models.ForeignKey(MenuSection, on_delete=models.SET_NULL, blank=True, null=True, related_name="items")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE')
    order_index = models.IntegerField(default=0)
    loyverse_stock = models.IntegerField(default=10, help_text="Live stock level in Loyverse POS")
    
    # Filter Flags (Dynamic Search Filters)
    is_hot = models.BooleanField(default=False)
    is_cold = models.BooleanField(default=False)
    is_coffee = models.BooleanField(default=False)
    is_milky = models.BooleanField(default=False)
    is_dairy_free = models.BooleanField(default=False)
    is_caffeine_free = models.BooleanField(default=False)
    show_fresh_badge = models.BooleanField(default=False, help_text="إظهار وسم 'متوفر طازجاً'")

    def __str__(self):
        return self.display_name


from django.contrib import admin
from .models import StaffUser, CustomerProfile, CafeConfiguration, ContactMessage

@admin.register(StaffUser)
class StaffUserAdmin(admin.ModelAdmin):
    list_display = ('employee_name', 'username', 'role', 'is_active', 'last_login')
    list_filter = ('role', 'is_active')
    search_fields = ('employee_name', 'username')

@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'phone_number', 'loyverse_customer_id', 'is_verified', 'created_at')
    list_filter = ('is_verified', 'created_at')
    search_fields = ('full_name', 'phone_number', 'loyverse_customer_id')

@admin.register(CafeConfiguration)
class CafeConfigurationAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'email', 'location_desc_en', 'opening_hours_en')
    
    def has_add_permission(self, request):
        # Prevent creating multiple configurations (Singleton pattern)
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ('name', 'contact', 'inquiry_type', 'is_read', 'created_at')
    list_filter = ('is_read', 'inquiry_type', 'created_at')
    search_fields = ('name', 'contact', 'message')
    readonly_fields = ('created_at',)


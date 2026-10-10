from django.contrib import admin
from .models import MenuCategory, MenuItem, MenuTab, MenuSection

@admin.register(MenuTab)
class MenuTabAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'name_ar', 'order_index', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name_en', 'name_ar')

@admin.register(MenuSection)
class MenuSectionAdmin(admin.ModelAdmin):
    list_display = ('name_en', 'name_ar', 'tab', 'order_index', 'is_active')
    list_filter = ('is_active', 'tab')
    search_fields = ('name_en', 'name_ar')

@admin.register(MenuCategory)
class MenuCategoryAdmin(admin.ModelAdmin):
    list_display = ('display_category_name', 'pos_category_name', 'loyverse_category_id', 'order_index', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('display_category_name', 'pos_category_name')

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'pos_name', 'category', 'custom_section', 'price', 'status', 'loyverse_stock', 'order_index')
    list_filter = ('status', 'category', 'custom_section')
    search_fields = ('display_name', 'pos_name', 'description')



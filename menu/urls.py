from django.urls import path
from . import views

app_name = 'menu'

urlpatterns = [
    path('', views.cover_page_view, name='cover_page'),
    path('menu/', views.client_menu_view, name='client_menu'),
    path('contact/', views.contact_view, name='contact'),
    path('contact/submit/', views.contact_submit, name='contact_submit'),
    path('api/webhooks/loyverse/', views.loyverse_webhook, name='loyverse_webhook'),
    path('api/staff/menu/edit/', views.staff_menu_edit, name='staff_menu_edit'),
    path('api/staff/tabs/edit/', views.staff_tab_edit, name='staff_tab_edit'),
    path('api/staff/sections/edit/', views.staff_section_edit, name='staff_section_edit'),
]


from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # Customer Loyalty portal
    path('loyalty/', views.loyalty_view, name='loyalty_view'),
    path('api/customer/register/', views.customer_register_request, name='customer_register'),
    path('api/customer/register/verify/', views.customer_register_verify, name='customer_verify'),
    path('api/customer/login/', views.customer_login, name='customer_login'),
    path('api/customer/logout/', views.customer_logout, name='customer_logout'),
    path('api/customer/qr-token/', views.get_qr_token, name='get_qr_token'),
    path('api/customer/redeem-voucher/', views.customer_redeem_voucher, name='customer_redeem_voucher'),
    
    path('api/customer/forgot-password/', views.customer_forgot_password_request, name='customer_forgot_password_request'),
    path('api/customer/reset-password/', views.customer_forgot_password_reset, name='customer_forgot_password_reset'),
    
    # Staff Dashboard
    path('staff/login/', views.staff_login_view, name='staff_login_view'),
    path('api/staff/login/', views.staff_login, name='staff_login_api'),
    path('staff/logout/', views.staff_logout, name='staff_logout'),
    path('staff/dashboard/', views.staff_dashboard_view, name='staff_dashboard'),
    path('staff/settings/', views.staff_settings_view, name='staff_settings'),
    path('staff/app/', views.staff_app_view, name='staff_app'),
    path('staff/download-app/', views.staff_download_apk, name='staff_download_apk'),
    path('api/staff/table-calls/status/', views.staff_table_calls_status, name='staff_table_calls_status'),
    path('api/staff/table-calls/accept/', views.staff_table_calls_accept, name='staff_table_calls_accept'),
    path('api/staff/push-subscribe/', views.staff_push_subscribe, name='staff_push_subscribe'),
    path('api/staff/messages/read/', views.mark_message_read, name='mark_message_read'),
    path('api/staff/config/edit/', views.staff_config_edit, name='staff_config_edit'),
    path('api/staff/menu/toggle-status/', views.staff_menu_toggle_status, name='staff_menu_toggle_status'),
    path('api/staff/whatsapp/logs/', views.get_whatsapp_logs, name='get_whatsapp_logs'),
    path('api/staff/whatsapp/test-send/', views.whatsapp_test_send, name='whatsapp_test_send'),
    path('api/staff/redeem-scan/', views.staff_redeem_scan, name='staff_redeem_scan'),
    path('api/staff/raffle/draw/', views.staff_raffle_draw, name='staff_raffle_draw'),
    path('api/staff/tab/manage/', views.staff_tab_manage, name='staff_tab_manage'),
    path('api/staff/section/manage/', views.staff_section_manage, name='staff_section_manage'),
    path('api/staff/group/items/add/', views.staff_group_items_add, name='staff_group_items_add'),
    path('api/staff/item/remove/', views.staff_item_remove_group, name='staff_item_remove_group'),
    path('api/staff/item/rename/', views.staff_item_rename, name='staff_item_rename'),
    path('api/staff/item/delete/', views.staff_item_delete, name='staff_item_delete'),
    path('api/staff/loyverse/sync/', views.staff_loyverse_sync_live, name='staff_loyverse_sync_live'),
    path('api/staff/users/list/', views.staff_users_list, name='staff_users_list'),
    path('api/staff/users/manage/', views.staff_user_manage, name='staff_user_manage'),
    path('api/staff/users/delete/', views.staff_user_delete, name='staff_user_delete'),
    path('api/staff/users/change-password/', views.staff_user_change_password, name='staff_user_change_password'),

    # Integrations
    path('integrations/loyverse-webhook/', views.loyverse_webhook, name='loyverse_webhook'),
    
    # Table Services
    path('table/<str:table_number>/', views.table_service_view, name='table_service'),
]

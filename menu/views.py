import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import render
from django.db import transaction
from .models import MenuCategory, MenuItem

@csrf_exempt
@require_POST
def loyverse_webhook(request):
    """
    Listens to Loyverse Webhooks (items.update, categories.update)
    and synchronizes them into local database models.
    """
    try:
        data = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    event_type = data.get("event_type")

    if not event_type:
        return JsonResponse({"status": "error", "message": "Missing event_type"}, status=400)

    try:
        with transaction.atomic():
            if event_type == "categories.update" or "categories" in data:
                categories = data.get("categories", [])
                for cat_data in categories:
                    cat_id = cat_data.get("id")
                    pos_name = cat_data.get("name", "")
                    
                    category, created = MenuCategory.objects.get_or_create(
                        loyverse_category_id=cat_id,
                        defaults={
                            "pos_category_name": pos_name,
                            "display_category_name": pos_name,  # default to pos_name
                            "category_image": cat_data.get("image_url", ""),
                            "is_active": not cat_data.get("deleted", False)
                        }
                    )
                    if not created:
                        category.pos_category_name = pos_name
                        if cat_data.get("image_url"):
                            category.category_image = cat_data.get("image_url")
                        category.is_active = not cat_data.get("deleted", False)
                        category.save()

            if event_type == "items.update" or "items" in data:
                items = data.get("items", [])
                for item_data in items:
                    item_id = item_data.get("id")
                    pos_name = item_data.get("name", "")
                    price = item_data.get("price", 0.0)
                    cat_id = item_data.get("category_id")
                    stock = item_data.get("stock", 10)  # mock or extracted stock level
                    
                    # Ensure category exists
                    if cat_id:
                        category, _ = MenuCategory.objects.get_or_create(
                            loyverse_category_id=cat_id,
                            defaults={
                                "pos_category_name": "Uncategorized",
                                "display_category_name": "Uncategorized"
                            }
                        )
                    else:
                        category, _ = MenuCategory.objects.get_or_create(
                            loyverse_category_id="default",
                            defaults={
                                "pos_category_name": "General",
                                "display_category_name": "General"
                            }
                        )

                    item, created = MenuItem.objects.get_or_create(
                        loyverse_id=item_id,
                        defaults={
                            "pos_name": pos_name,
                            "display_name": pos_name,  # default to pos_name
                            "price": price,
                            "category": category,
                            "loyverse_stock": stock,
                            "image": item_data.get("image_url", "")
                        }
                    )
                    if not created:
                        item.pos_name = pos_name
                        item.price = price
                        item.category = category
                        item.loyverse_stock = stock
                        if item_data.get("image_url"):
                            item.image = item_data.get("image_url")
                        item.save()

        return JsonResponse({"status": "success", "message": "Synced successfully"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

@csrf_exempt
@require_POST
def staff_menu_edit(request):
    """
    Secure endpoint allowing staff/admin users to override MenuItems.
    Validates if they are authenticated as a StaffUser via session.
    """
    # Verify session authentication for Staff
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)

    try:
        # Check if form data or JSON
        if request.content_type == "application/json":
            data = json.loads(request.body.decode('utf-8'))
        else:
            data = request.POST

        item_id = data.get("item_id")
        display_name = data.get("display_name")
        description = data.get("description")
        image = data.get("image")
        status = data.get("status")
        price = data.get("price")
        order_index = data.get("order_index")
        custom_section_id = data.get("custom_section_id")

        # Parse checkboxes / boolean values
        def is_checked(val):
            return str(val).lower() in ['on', 'true', '1']

        is_hot = is_checked(data.get("is_hot"))
        is_cold = is_checked(data.get("is_cold"))
        is_coffee = is_checked(data.get("is_coffee"))
        is_milky = is_checked(data.get("is_milky"))
        is_dairy_free = is_checked(data.get("is_dairy_free"))
        is_caffeine_free = is_checked(data.get("is_caffeine_free"))
        show_fresh_badge = is_checked(data.get("show_fresh_badge"))

        try:
            item = MenuItem.objects.get(id=item_id)
        except MenuItem.DoesNotExist:
            return JsonResponse({"status": "error", "message": "MenuItem not found"}, status=404)

        if display_name:
            item.display_name = display_name
        if description is not None:
            item.description = description
        if image is not None:
            item.image = image
        if status:
            item.status = status
        if price:
            item.price = float(price)
        if order_index is not None:
            item.order_index = int(order_index)

        # Update section assignment
        if custom_section_id:
            from .models import MenuSection
            try:
                item.custom_section = MenuSection.objects.get(id=custom_section_id)
            except MenuSection.DoesNotExist:
                item.custom_section = None
        else:
            item.custom_section = None

        # Update flags
        item.is_hot = is_hot
        item.is_cold = is_cold
        item.is_coffee = is_coffee
        item.is_milky = is_milky
        item.is_dairy_free = is_dairy_free
        item.is_caffeine_free = is_caffeine_free
        item.show_fresh_badge = show_fresh_badge

        item.save()
        return JsonResponse({"status": "success", "message": f"Updated {item.display_name} successfully"})
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

def client_menu_view(request):
    """
    Renders the upgraded customer-facing menu with tabs and sections.
    Filters out MenuItem instances where status == 'OUT_OF_STOCK'.
    If status == 'AUTOMATIC', excludes item if its loyverse_stock <= 0.
    """
    from .models import MenuTab, MenuSection, MenuItem
    
    # Get all active Tabs
    tabs = MenuTab.objects.filter(is_active=True).order_by('order_index')
    
    # Compile tabs data with sections and their items
    tabs_data = []
    for tab in tabs:
        sections = tab.sections.filter(is_active=True).order_by('order_index')
        sections_data = []
        for section in sections:
            # Exclude OUT_OF_STOCK items
            items = section.items.exclude(status='OUT_OF_STOCK').order_by('order_index')
            # Filter out AUTOMATIC items with zero/negative stock
            filtered_items = []
            for item in items:
                if item.status == 'AUTOMATIC' and item.loyverse_stock <= 0:
                    continue
                filtered_items.append(item)
            
            if filtered_items:
                sections_data.append({
                    "section": section,
                    "items": filtered_items
                })
        
        # Only include tab if it has active sections with items
        if sections_data:
            tabs_data.append({
                "tab": tab,
                "sections": sections_data
            })
            
    # For fallback (items that don't belong to any custom section)
    uncategorized_items = MenuItem.objects.filter(custom_section__isnull=True).exclude(status='OUT_OF_STOCK').order_by('order_index')
    filtered_uncategorized = []
    for item in uncategorized_items:
        if item.status == 'AUTOMATIC' and item.loyverse_stock <= 0:
            continue
        filtered_uncategorized.append(item)
        
    if filtered_uncategorized:
        # Create a mock tab and section for General/Other items
        fallback_tab = MenuTab(name_en="New Additions", name_ar="إضافات جديدة", id=9999)
        fallback_section = MenuSection(name_en="Recently Synced from POS", name_ar="مضافة حديثاً من الكاشير", banner_image="https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?q=80&w=1000")
        tabs_data.append({
            "tab": fallback_tab,
            "sections": [{
                "section": fallback_section,
                "items": filtered_uncategorized
            }]
        })

    return render(request, "menu/index.html", {"tabs_data": tabs_data})


def cover_page_view(request):
    """
    Renders the premium cover landing page for 26 Cafe.
    """
    return render(request, "menu/cover.html")

def contact_view(request):
    """
    Renders the premium contact page for 26 Cafe.
    """
    from accounts.models import CafeConfiguration
    config = CafeConfiguration.objects.first()
    if not config:
        config = CafeConfiguration.objects.create() # fallback defaults
    return render(request, "menu/contact.html", {"config": config})

@csrf_exempt
@require_POST
def contact_submit(request):
    """
    Handles visitor contact form submissions.
    Saves the message, alerts staff via WebSocket, and triggers a WhatsApp notification.
    """
    from accounts.models import ContactMessage, CafeConfiguration
    from cafe_smart.integrations import WhatsAppClient
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync

    name = request.POST.get("name")
    contact = request.POST.get("contact")
    inquiry_type = request.POST.get("type", "General Inquiry")
    message = request.POST.get("message")

    if not all([name, contact, message]):
        return JsonResponse({"status": "error", "message": "All fields are required"}, status=400)

    # Save message to database
    msg = ContactMessage.objects.create(
        name=name,
        contact=contact,
        inquiry_type=inquiry_type,
        message=message
    )

    # Mock WhatsApp notification to the owner's configured WhatsApp number
    config = CafeConfiguration.objects.first()
    owner_whatsapp = config.whatsapp_number if (config and config.whatsapp_number) else "962792626262"
    
    # Trigger message log
    whatsapp_text = f"📢 *New Inquiry for 26 Cafe*\n\n👤 *From:* {name}\n📞 *Contact:* {contact}\n📌 *Type:* {inquiry_type}\n💬 *Message:* {message}"
    
    # We can print to terminal and log in WhatsAppClient list
    WhatsAppClient.sent_messages.append({
        "phone_number": owner_whatsapp,
        "type": "OWNER_INQUIRY_NOTIFICATION",
        "message": whatsapp_text
    })
    try:
        print(f"\n========================================\n[OWNER NOTIFICATION SENT VIA WHATSAPP]\nTo: {owner_whatsapp}\n{whatsapp_text}\n========================================\n")
    except UnicodeEncodeError:
        safe_text = whatsapp_text.encode('ascii', errors='replace').decode('ascii')
        print(f"\n========================================\n[OWNER NOTIFICATION SENT VIA WHATSAPP]\nTo: {owner_whatsapp}\n{safe_text}\n========================================\n")


    # Broadcast to staff_dashboard WebSocket group
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            "staff_dashboard",
            {
                "type": "broadcast_contact_message",
                "message": {
                    "id": msg.id,
                    "name": msg.name,
                    "contact": msg.contact,
                    "inquiry_type": msg.inquiry_type,
                    "message": msg.message,
                    "created_at": msg.created_at.strftime("%I:%M %p")
                }
            }
        )

    return JsonResponse({"status": "success", "message": "Message sent successfully!"})

@csrf_exempt
@require_POST
def staff_tab_edit(request):
    """
    Saves or creates a custom tab for the digital menu.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    role = request.session.get("staff_role")
    if role != "ADMIN":
        return JsonResponse({"status": "error", "message": "Admin privileges required"}, status=403)

    tab_id = request.POST.get("tab_id")
    name_en = request.POST.get("name_en")
    name_ar = request.POST.get("name_ar")
    order_index = request.POST.get("order_index", 0)
    is_active = request.POST.get("is_active", "true").lower() == "true"

    if not name_en or not name_ar:
        return JsonResponse({"status": "error", "message": "Both English and Arabic names are required"}, status=400)

    from .models import MenuTab
    if tab_id:
        try:
            tab = MenuTab.objects.get(id=tab_id)
        except MenuTab.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Tab not found"}, status=404)
    else:
        tab = MenuTab()

    tab.name_en = name_en
    tab.name_ar = name_ar
    tab.order_index = int(order_index)
    tab.is_active = is_active
    tab.save()

    return JsonResponse({"status": "success", "message": "Tab saved successfully!", "tab_id": tab.id})

@csrf_exempt
@require_POST
def staff_section_edit(request):
    """
    Saves or creates a custom section under a tab.
    """
    if not request.session.get("staff_user_id"):
        return JsonResponse({"status": "error", "message": "Unauthorized"}, status=401)
    
    role = request.session.get("staff_role")
    if role != "ADMIN":
        return JsonResponse({"status": "error", "message": "Admin privileges required"}, status=403)

    section_id = request.POST.get("section_id")
    tab_id = request.POST.get("tab_id")
    name_en = request.POST.get("name_en")
    name_ar = request.POST.get("name_ar")
    banner_image = request.POST.get("banner_image")
    order_index = request.POST.get("order_index", 0)
    is_active = request.POST.get("is_active", "true").lower() == "true"

    if not name_en or not name_ar or not tab_id:
        return JsonResponse({"status": "error", "message": "English name, Arabic name, and Tab selection are required"}, status=400)

    from .models import MenuSection, MenuTab
    if section_id:
        try:
            section = MenuSection.objects.get(id=section_id)
        except MenuSection.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Section not found"}, status=404)
    else:
        section = MenuSection()

    try:
        tab = MenuTab.objects.get(id=tab_id)
    except MenuTab.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Invalid Tab selection"}, status=400)

    section.tab = tab
    section.name_en = name_en
    section.name_ar = name_ar
    section.banner_image = banner_image
    section.order_index = int(order_index)
    section.is_active = is_active
    section.save()

    return JsonResponse({"status": "success", "message": "Section saved successfully!", "section_id": section.id})





"""
Search engine helpers: robots.txt, sitemap.xml and schema.org structured data
(read by Google and by AI assistants to understand the cafe and its menu).
"""
import json
from django.http import HttpResponse
from django.utils.safestring import mark_safe

PUBLIC_PAGES = ["/", "/menu/", "/contact/", "/loyalty/"]


def _absolute(request, path):
    return request.build_absolute_uri(path)


def json_ld(data):
    """Renders a <script type="application/ld+json"> block safely inside HTML."""
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    return mark_safe(f'<script type="application/ld+json">{payload}</script>')


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /staff/",
        "Disallow: /api/",
        "Disallow: /admin/",
        "Disallow: /table/",
        "Disallow: /integrations/",
        "",
        f"Sitemap: {_absolute(request, '/sitemap.xml')}",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def sitemap_xml(request):
    urls = "".join(
        f"<url><loc>{_absolute(request, path)}</loc><changefreq>weekly</changefreq>"
        f"<priority>{'1.0' if path == '/' else '0.8'}</priority></url>"
        for path in PUBLIC_PAGES
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


def cafe_schema(request, config):
    if not config:
        return ""
    data = {
        "@context": "https://schema.org",
        "@type": "CafeOrCoffeeShop",
        "name": config.cafe_name,
        "alternateName": config.hero_title_ar,
        "description": config.hero_subtitle_ar,
        "url": _absolute(request, "/"),
        "image": share_image_url(request, config),
        "telephone": config.phone_number,
        "email": config.email,
        "servesCuisine": ["Coffee", "Specialty Coffee", "Desserts"],
        "hasMenu": _absolute(request, "/menu/"),
        "address": {
            "@type": "PostalAddress",
            "streetAddress": config.location_desc_ar,
            "addressLocality": "Wadi Musa",
            "addressRegion": "Ma'an",
            "addressCountry": "JO",
        },
        "geo": {"@type": "GeoCoordinates", "latitude": config.latitude, "longitude": config.longitude},
        "sameAs": [u for u in [config.instagram_url, config.facebook_url, config.tiktok_url] if u],
    }
    return json_ld(data)


def menu_schema(request, config, tabs_data):
    sections = []
    for tab in tabs_data:
        for block in tab["sections"]:
            items = [{
                "@type": "MenuItem",
                "name": item.display_name or item.pos_name,
                **({"description": item.description} if item.description else {}),
                "offers": {"@type": "Offer", "price": f"{item.price:.3f}", "priceCurrency": "JOD"},
            } for item in block["items"]]
            sections.append({
                "@type": "MenuSection",
                "name": f'{block["section"].name_ar} / {block["section"].name_en}',
                "hasMenuItem": items,
            })
    data = {
        "@context": "https://schema.org",
        "@type": "Menu",
        "name": f"{config.cafe_name if config else 'Cafe'} Menu",
        "url": _absolute(request, "/menu/"),
        "inLanguage": ["ar", "en"],
        "hasMenuSection": sections,
    }
    return json_ld(data)


def share_image_url(request, config):
    if config and config.menu_cover_image:
        return _absolute(request, config.menu_cover_image.url)
    return _absolute(request, "/static/images/logo.jpg")

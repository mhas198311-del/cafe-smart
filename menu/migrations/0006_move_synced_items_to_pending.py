from django.db import migrations


def move_to_pending(apps, schema_editor):
    # The first live sync dropped every Loyverse item into the first hot/cold group.
    # Send them back to the pending list so staff can place them by hand.
    # Demo items from seed.py (ids starting with "item-") are left alone.
    MenuItem = apps.get_model("menu", "MenuItem")
    MenuItem.objects.exclude(loyverse_id__startswith="item-").update(custom_section=None)


class Migration(migrations.Migration):

    dependencies = [
        ("menu", "0005_menuitem_show_fresh_badge"),
    ]

    operations = [
        migrations.RunPython(move_to_pending, migrations.RunPython.noop),
    ]

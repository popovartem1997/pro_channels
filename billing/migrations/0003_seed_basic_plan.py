from django.db import migrations


def seed_basic_plan(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    if Plan.objects.filter(code="basic").exists():
        return
    Plan.objects.create(
        code="basic",
        name="1 канал",
        price=299.00,
        channels_limit=1,
        duration_days=30,
        is_active=True,
        description="Подписка на 1 канал/паблик (299 ₽/мес).",
        features=[
            "Публикация постов",
            "Планировщик и повторы",
            "Боты предложки",
            "Статистика",
            "Парсинг",
            "Реклама",
        ],
    )


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0002_invoice_channel"),
    ]

    operations = [
        migrations.RunPython(seed_basic_plan, migrations.RunPython.noop),
    ]


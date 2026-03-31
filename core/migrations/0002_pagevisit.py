from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PageVisit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("method", models.CharField(blank=True, max_length=10)),
                ("path", models.CharField(db_index=True, max_length=500)),
                ("query_string", models.TextField(blank=True)),
                ("referer", models.CharField(blank=True, max_length=500)),
                ("user_agent", models.TextField(blank=True)),
                ("ip", models.CharField(blank=True, max_length=64)),
                ("status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="page_visits", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Посещение страницы",
                "verbose_name_plural": "Посещения страниц",
                "ordering": ["-created_at"],
            },
        ),
    ]


# Generated manually: единый источник Telegram/MAX ID — профиль User.

from django.db import migrations


def forwards(apps, schema_editor):
    TeamMember = apps.get_model('managers', 'TeamMember')
    User = apps.get_model('accounts', 'User')

    for tm in TeamMember.objects.select_related('member').iterator():
        u = tm.member
        u_changed = False
        if u.telegram_user_id is None and tm.telegram_user_id is not None:
            u.telegram_user_id = tm.telegram_user_id
            u_changed = True
        u_max = (u.max_user_id or '').strip()
        tm_max = (tm.max_user_id or '').strip()
        if not u_max and tm_max:
            u.max_user_id = tm_max
            u_changed = True
        if u_changed:
            User.objects.filter(pk=u.pk).update(
                telegram_user_id=u.telegram_user_id,
                max_user_id=u.max_user_id or '',
            )

    for tm in TeamMember.objects.select_related('member').iterator():
        u = tm.member
        if tm.telegram_user_id != u.telegram_user_id or (tm.max_user_id or '') != (u.max_user_id or ''):
            TeamMember.objects.filter(pk=tm.pk).update(
                telegram_user_id=u.telegram_user_id,
                max_user_id=u.max_user_id or '',
            )


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('managers', '0002_teammember_platform_ids'),
        ('accounts', '0005_user_telegram_max_ids'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

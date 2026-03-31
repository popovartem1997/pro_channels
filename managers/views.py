"""
Управление командой: менеджеры, роли, права.
"""
import secrets
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import TeamInvite, TeamMember


def _require_owner(request):
    # Владелец или staff может управлять командой
    if not (request.user.is_staff or getattr(request.user, 'role', '') == 'owner'):
        from django.http import HttpResponse
        return HttpResponse(status=403)
    return None


@login_required
def team_list(request):
    resp = _require_owner(request)
    if resp:
        return resp
    members = TeamMember.objects.filter(owner=request.user, is_active=True).select_related('member')
    invites = TeamInvite.objects.filter(owner=request.user, status=TeamInvite.STATUS_PENDING)
    return render(request, 'managers/list.html', {
        'members': members,
        'invites': invites,
    })


@login_required
def team_invite(request):
    resp = _require_owner(request)
    if resp:
        return resp
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        role = request.POST.get('role', TeamInvite.ROLE_MANAGER)
        if not email:
            messages.error(request, 'Введите email.')
            return redirect('managers:list')
        # Проверяем что не повторный инвайт
        if TeamInvite.objects.filter(owner=request.user, email=email, status=TeamInvite.STATUS_PENDING).exists():
            messages.warning(request, f'Приглашение для {email} уже отправлено.')
            return redirect('managers:list')
        invite = TeamInvite.objects.create(owner=request.user, email=email, role=role)
        # Отправка письма
        _send_invite_email(invite, request)
        messages.success(request, f'Приглашение отправлено на {email}.')
        return redirect('managers:list')
    return render(request, 'managers/invite.html', {'roles': TeamInvite.ROLE_CHOICES})


@login_required
def team_create_account(request):
    """
    Создать аккаунт менеджера/помощника сразу (логин+пароль готовые),
    без email-инвайта.
    """
    resp = _require_owner(request)
    if resp:
        return resp
    if request.method != 'POST':
        return redirect('managers:list')

    from accounts.models import User

    email = request.POST.get('email', '').strip().lower()
    username = request.POST.get('username', '').strip()
    role = request.POST.get('role', TeamInvite.ROLE_MANAGER)

    if not username:
        messages.error(request, 'Введите логин (username).')
        return redirect('managers:list')

    if User.objects.filter(username=username).exists():
        messages.error(request, 'Такой логин уже занят.')
        return redirect('managers:list')

    if email and User.objects.filter(email=email).exists():
        messages.error(request, 'Такой email уже зарегистрирован.')
        return redirect('managers:list')

    password = secrets.token_urlsafe(12)

    user = User.objects.create_user(
        username=username,
        email=email or '',
        password=password,
        role=role,
        invited_by=request.user,
        is_email_verified=bool(email),
    )

    TeamMember.objects.get_or_create(
        owner=request.user,
        member=user,
        defaults={'role': role},
    )

    # Показываем пароль один раз
    return render(request, 'managers/created_account.html', {
        'created_user': user,
        'created_password': password,
    })


def _send_invite_email(invite, request):
    from django.core.mail import send_mail
    from django.conf import settings
    accept_url = request.build_absolute_uri(f'/managers/accept/{invite.token}/')
    send_mail(
        subject=f'Приглашение в команду ProChannels',
        message=(
            f'Вас приглашают присоединиться к команде в ProChannels.\n\n'
            f'Роль: {invite.get_role_display()}\n\n'
            f'Принять приглашение: {accept_url}\n\n'
            f'Приглашение действительно 7 дней.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invite.email],
        fail_silently=True,
    )


def accept_invite(request, token):
    invite = get_object_or_404(TeamInvite, token=token, status=TeamInvite.STATUS_PENDING)
    if invite.is_expired:
        invite.status = TeamInvite.STATUS_EXPIRED
        invite.save()
        messages.error(request, 'Приглашение истекло.')
        return redirect('home')

    if not request.user.is_authenticated:
        from django.contrib.auth import REDIRECT_FIELD_NAME
        return redirect(f'/register/?next=/managers/accept/{token}/')

    if request.method == 'POST':
        # Создаём члена команды
        member, created = TeamMember.objects.get_or_create(
            owner=invite.owner,
            member=request.user,
            defaults={'role': invite.role}
        )
        if not created:
            member.role = invite.role
            member.is_active = True
            member.save()

        invite.status = TeamInvite.STATUS_ACCEPTED
        invite.accepted_by = request.user
        invite.save()

        messages.success(request, f'Вы вступили в команду {invite.owner}.')
        return redirect('dashboard')

    return render(request, 'managers/accept_invite.html', {'invite': invite})


@login_required
def member_remove(request, pk):
    resp = _require_owner(request)
    if resp:
        return resp
    member = get_object_or_404(TeamMember, pk=pk, owner=request.user)
    if request.method == 'POST':
        member.is_active = False
        member.save()
        messages.success(request, f'Менеджер {member.member} удалён из команды.')
    return redirect('managers:list')


@login_required
def invite_cancel(request, pk):
    resp = _require_owner(request)
    if resp:
        return resp
    invite = get_object_or_404(TeamInvite, pk=pk, owner=request.user, status=TeamInvite.STATUS_PENDING)
    if request.method == 'POST':
        invite.status = TeamInvite.STATUS_DECLINED
        invite.save(update_fields=['status'])
        messages.success(request, 'Приглашение отменено.')
    return redirect('managers:list')

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from .models import User, EmailVerification, PasswordResetToken
from .forms import RegisterForm, LoginForm, ProfileForm


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_email_verified = False
            user.save()
            # Создаём токен для подтверждения email
            verification = EmailVerification.objects.create(user=user)
            _send_verification_email(user, verification, request)
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(
                request,
                f'Добро пожаловать! У вас {user.trial_days_left} дней бесплатного доступа. '
                f'Проверьте email для подтверждения.'
            )
            return redirect('dashboard')
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})


def _send_verification_email(user, verification, request):
    verify_url = request.build_absolute_uri(f'/verify-email/{verification.token}/')
    send_mail(
        subject='Подтвердите email — ProChannels',
        message=(
            f'Здравствуйте, {user.first_name or user.username}!\n\n'
            f'Для подтверждения email перейдите по ссылке:\n{verify_url}\n\n'
            f'Ссылка действительна 24 часа.'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def verify_email(request, token):
    verification = get_object_or_404(EmailVerification, token=token, is_used=False)
    if verification.is_expired():
        messages.error(request, 'Ссылка истекла. Запросите новую.')
        return redirect('dashboard')
    verification.is_used = True
    verification.save()
    verification.user.is_email_verified = True
    verification.user.save(update_fields=['is_email_verified'])
    messages.success(request, 'Email успешно подтверждён!')
    return redirect('dashboard')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(request.GET.get('next', 'dashboard'))
        messages.error(request, 'Неверный email или пароль.')
    else:
        form = LoginForm()
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('home')


def reset_password_request(request):
    """Форма запроса сброса пароля."""
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        try:
            user = User.objects.get(email=email)
            token_obj = PasswordResetToken.objects.create(user=user)
            reset_url = request.build_absolute_uri(f'/reset-password/{token_obj.token}/')
            send_mail(
                subject='Сброс пароля — ProChannels',
                message=(
                    f'Для сброса пароля перейдите по ссылке:\n{reset_url}\n\n'
                    f'Ссылка действительна 1 час.\n'
                    f'Если вы не запрашивали сброс — проигнорируйте это письмо.'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
        except User.DoesNotExist:
            pass  # Не раскрываем существование email
        messages.success(request, 'Если указанный email зарегистрирован, письмо отправлено.')
        return redirect('login')
    return render(request, 'accounts/reset_password_request.html')


def reset_password_confirm(request, token):
    """Форма ввода нового пароля."""
    token_obj = get_object_or_404(PasswordResetToken, token=token, is_used=False)
    if token_obj.is_expired():
        messages.error(request, 'Ссылка истекла.')
        return redirect('login')

    if request.method == 'POST':
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        if password1 != password2:
            messages.error(request, 'Пароли не совпадают.')
        elif len(password1) < 8:
            messages.error(request, 'Пароль должен быть не менее 8 символов.')
        else:
            user = token_obj.user
            user.set_password(password1)
            user.save()
            token_obj.is_used = True
            token_obj.save()
            messages.success(request, 'Пароль изменён. Войдите с новым паролем.')
            return redirect('login')

    return render(request, 'accounts/reset_password_confirm.html', {'token': token})


@login_required
def profile(request):
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Профиль обновлён.')
            return redirect('profile')
    else:
        form = ProfileForm(instance=request.user)
    return render(request, 'accounts/profile.html', {'form': form})


@login_required
def dashboard(request):
    from bots.models import SuggestionBot, Suggestion
    from channels.models import Channel
    from content.models import Post

    user = request.user
    if user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        memberships = TeamMember.objects.filter(member=user, is_active=True).select_related('owner').prefetch_related('channels')
        assigned_channels = Channel.objects.filter(
            pk__in=TeamMember.objects.filter(member=user, is_active=True).values_list('channels__pk', flat=True)
        ).distinct()
        recent_posts = Post.objects.filter(channels__in=assigned_channels).distinct().order_by('-created_at')[:10]
        return render(request, 'accounts/dashboard_manager.html', {
            'memberships': memberships,
            'assigned_channels': assigned_channels,
            'recent_posts': recent_posts,
        })
    bots = SuggestionBot.objects.filter(owner=user)
    channels = Channel.objects.filter(owner=user)
    pending_count = Suggestion.objects.filter(bot__owner=user, status='pending').count()
    recent_posts = Post.objects.filter(author=user).order_by('-created_at')[:5]
    scheduled_posts = Post.objects.filter(
        author=user, status='scheduled'
    ).order_by('scheduled_at')[:5]

    today = timezone.now().date()
    posts_today = Post.objects.filter(
        author=user, created_at__date=today
    ).count()

    return render(request, 'accounts/dashboard.html', {
        'bots': bots,
        'channels': channels,
        'pending_count': pending_count,
        'recent_posts': recent_posts,
        'scheduled_posts': scheduled_posts,
        'channels_count': channels.count(),
        'bots_count': bots.count(),
        'posts_today': posts_today,
    })


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Пароль изменён.')
            return redirect('profile')
        messages.error(request, 'Проверьте поля формы.')
    else:
        form = PasswordChangeForm(user=request.user)
    for f in form.fields.values():
        f.widget.attrs['class'] = 'form-control'
    return render(request, 'accounts/change_password.html', {'form': form})

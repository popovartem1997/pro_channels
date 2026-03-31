"""
Бэкенд аутентификации — логин по email или username.
"""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()


class EmailOrUsernameBackend(ModelBackend):
    """Позволяет войти как по email, так и по username."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        # Ищем по email (основной способ)
        try:
            user = User.objects.get(email__iexact=username)
        except User.DoesNotExist:
            # Пробуем по username (для суперпользователей типа 'admin')
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

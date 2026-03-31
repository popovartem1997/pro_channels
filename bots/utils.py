"""
Утилиты для шифрования/дешифрования токенов ботов.

Токены хранятся в БД в зашифрованном виде с помощью Fernet (симметричное шифрование).
Ключ шифрования задаётся в settings.py -> BOTS_ENCRYPTION_KEY.
"""
from cryptography.fernet import Fernet
from django.conf import settings


def _get_fernet() -> Fernet:
    key = getattr(settings, 'BOTS_ENCRYPTION_KEY', None)
    if not key:
        raise ValueError(
            "BOTS_ENCRYPTION_KEY не задан в settings.py. "
            "Сгенерируйте ключ: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    """Зашифровать токен бота перед сохранением в БД."""
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Расшифровать токен бота для использования в API-вызовах."""
    return _get_fernet().decrypt(encrypted_token.encode()).decode()

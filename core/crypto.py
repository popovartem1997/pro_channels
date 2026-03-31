"""
Централизованное шифрование токенов.
Используется во всех приложениях (bots, channels и т.д.)
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet:
    key = settings.BOTS_ENCRYPTION_KEY
    if not key:
        raise ValueError(
            'BOTS_ENCRYPTION_KEY не задан в настройках. '
            'Сгенерируйте командой: '
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt_token(token: str) -> str:
    """Зашифровать токен. Возвращает зашифрованную строку."""
    if not token:
        return ''
    return _get_fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Расшифровать токен. Возвращает исходную строку."""
    if not encrypted_token:
        return ''
    try:
        return _get_fernet().decrypt(encrypted_token.encode()).decode()
    except (InvalidToken, Exception) as e:
        logger.error(f'Ошибка расшифровки токена: {e}')
        return ''

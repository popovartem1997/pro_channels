"""
Клиент TBank Acquiring API.
Документация: https://www.tbank.ru/kassa/develop/api/payments/
"""
import hashlib
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class TBankClient:
    def __init__(self):
        self.terminal_key = settings.TBANK_TERMINAL_KEY
        self.secret_key = settings.TBANK_SECRET_KEY
        self.api_url = settings.TBANK_API_URL

    def _get_token(self, params: dict) -> str:
        """Генерация токена для подписи запроса."""
        token_params = {**params, 'Password': self.secret_key}
        token_params.pop('Token', None)
        token_params.pop('DATA', None)
        token_params.pop('Receipt', None)
        sorted_values = ''.join(str(v) for _, v in sorted(token_params.items()))
        return hashlib.sha256(sorted_values.encode()).hexdigest()

    def _post(self, method: str, params: dict) -> dict:
        params['TerminalKey'] = self.terminal_key
        params['Token'] = self._get_token(params)
        try:
            resp = requests.post(f'{self.api_url}{method}', json=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f'TBank API error ({method}): {e}')
            return {'Success': False, 'Message': str(e)}

    def init_payment(self, order_id: str, amount: int, description: str,
                     customer_email: str = '') -> dict:
        """Инициализация платежа. Возвращает PaymentURL для редиректа."""
        params = {
            'OrderId': order_id,
            'Amount': amount,
            'Description': description,
            'NotificationURL': f'{settings.SITE_URL}/billing/webhook/tbank/',
            'SuccessURL': f'{settings.SITE_URL}/billing/success/',
            'FailURL': f'{settings.SITE_URL}/billing/fail/',
        }
        if customer_email:
            params['DATA'] = {'Email': customer_email}
        return self._post('Init', params)

    def get_state(self, payment_id: str) -> dict:
        """Получить текущий статус платежа."""
        return self._post('GetState', {'PaymentId': payment_id})

    def cancel_payment(self, payment_id: str) -> dict:
        """Отменить платёж."""
        return self._post('Cancel', {'PaymentId': payment_id})

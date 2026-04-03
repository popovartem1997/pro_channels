"""
Клиент TBank Acquiring API.
Документация: https://developer.tbank.ru/eacq/intro/developer/
Фискализация: объект Receipt в Init (настройка кассы / Чеки Т-Бизнес в кабинете T-Bank).
"""
import hashlib
import requests
import logging
from django.conf import settings
from core.models import get_global_api_keys

logger = logging.getLogger(__name__)


class TBankClient:
    def __init__(self):
        keys = get_global_api_keys()
        self.terminal_key = (keys.get_tbank_terminal_key() or '').strip()
        self.secret_key = (keys.get_tbank_secret_key() or '').strip()
        self.api_url = settings.TBANK_API_URL
        if not self.terminal_key or not self.secret_key:
            raise ValueError('TBank ключи не заданы (Ключи API → TBank).')

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

    def init_payment(
        self,
        order_id: str,
        amount: int,
        description: str,
        customer_email: str = '',
        *,
        send_fiscal_receipt: bool | None = None,
    ) -> dict:
        """Инициализация платежа. Возвращает PaymentURL для редиректа."""
        if send_fiscal_receipt is None:
            send_fiscal_receipt = bool(getattr(settings, 'TBANK_SEND_FISCAL_RECEIPT', True))
        params = {
            'OrderId': order_id,
            'Amount': amount,
            'Description': description[:250],
            'NotificationURL': f'{settings.SITE_URL}/billing/webhook/tbank/',
            'SuccessURL': f'{settings.SITE_URL}/billing/success/',
            'FailURL': f'{settings.SITE_URL}/billing/fail/',
        }
        if customer_email:
            params['DATA'] = {'Email': customer_email}
        if send_fiscal_receipt and customer_email and amount > 0:
            taxation = (getattr(settings, 'TBANK_RECEIPT_TAXATION', None) or 'usn_income').strip()
            item_name = (description or 'Услуга')[:128]
            params['Receipt'] = {
                'Email': customer_email,
                'Taxation': taxation,
                'Items': [
                    {
                        'Name': item_name,
                        'Price': amount,
                        'Quantity': 1.0,
                        'Amount': amount,
                        'Tax': 'none',
                        'PaymentMethod': 'full_payment',
                        'PaymentObject': 'service',
                    }
                ],
            }
        return self._post('Init', params)

    def get_state(self, payment_id: str) -> dict:
        """Получить текущий статус платежа."""
        return self._post('GetState', {'PaymentId': payment_id})

    def cancel_payment(self, payment_id: str) -> dict:
        """Отменить платёж."""
        return self._post('Cancel', {'PaymentId': payment_id})

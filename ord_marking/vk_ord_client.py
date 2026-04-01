"""
Клиент HTTP API ОРД VK (не api.vk.com/method).

Документация: https://ord.vk.com/help/api/api.html
Авторизация: Authorization: Bearer <ключ из кабинета ord.vk.com>
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OrdVkApiError(Exception):
    def __init__(self, status: int, body: str, parsed: dict | None = None):
        self.status = status
        self.body = body
        self.parsed = parsed or {}
        msg = self.parsed.get('error') or body[:500] or f'HTTP {status}'
        super().__init__(msg)


def _ord_base_url(use_sandbox: bool) -> str:
    return 'https://api-sandbox.ord.vk.com' if use_sandbox else 'https://api.ord.vk.com'


def ord_request(
    bearer: str,
    method: str,
    path: str,
    *,
    use_sandbox: bool = False,
    json_body: dict | list | None = None,
    timeout: int = 45,
) -> tuple[int, Any]:
    """Выполнить запрос к api.ord.vk.com. Возвращает (status, parsed_json|None)."""
    if not bearer or not bearer.strip():
        raise ValueError('Пустой ключ API ОРД (Bearer)')
    url = _ord_base_url(use_sandbox).rstrip('/') + path
    data = None
    headers = {'Authorization': f'Bearer {bearer.strip()}'}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            status = resp.getcode() or 200
            if not raw.strip():
                return status, {}
            try:
                return status, json.loads(raw)
            except json.JSONDecodeError:
                return status, {'_raw': raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace') if e.fp else ''
        parsed = None
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {'_raw': raw}
        raise OrdVkApiError(e.code, raw, parsed) from e


def put_creative_v2(
    bearer: str,
    external_id: str,
    body: dict,
    *,
    use_sandbox: bool = False,
) -> dict:
    """PUT /v2/creative/{external_id} — создание/обновление креатива. Ответ: marker, erid."""
    from urllib.parse import quote

    safe_id = quote(str(external_id), safe='')
    status, data = ord_request(
        bearer,
        'PUT',
        f'/v2/creative/{safe_id}',
        use_sandbox=use_sandbox,
        json_body=body,
    )
    if status not in (200, 201):
        raise OrdVkApiError(status, json.dumps(data, ensure_ascii=False), data if isinstance(data, dict) else {})
    if not isinstance(data, dict):
        raise OrdVkApiError(status, str(data), {})
    return data


def fetch_erid_map(
    bearer: str,
    *,
    use_sandbox: bool = False,
    limit: int = 500,
) -> list[dict]:
    """GET /v1/creative/list/erid_external_ids — пары erid ↔ external_id."""
    status, data = ord_request(
        bearer,
        'GET',
        f'/v1/creative/list/erid_external_ids?limit={limit}',
        use_sandbox=use_sandbox,
    )
    if status != 200:
        raise OrdVkApiError(status, json.dumps(data, ensure_ascii=False), data if isinstance(data, dict) else {})
    items = (data or {}).get('items') if isinstance(data, dict) else None
    return list(items or [])


def post_statistics_v1(
    bearer: str,
    items: list[dict],
    *,
    use_sandbox: bool = False,
) -> dict:
    """POST /v1/statistics — передать показы за период (в пределах одного месяца)."""
    status, data = ord_request(
        bearer,
        'POST',
        '/v1/statistics',
        use_sandbox=use_sandbox,
        json_body={'items': items},
    )
    if status != 200:
        raise OrdVkApiError(status, json.dumps(data, ensure_ascii=False), data if isinstance(data, dict) else {})
    return data if isinstance(data, dict) else {}

"""
Клиент HTTP API ОРД VK (не api.vk.com/method).

Документация: https://ord.vk.com/help/api/api.html
Авторизация: Authorization: Bearer <ключ из кабинета ord.vk.com>
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import time
from typing import Any
from urllib.parse import quote, urlencode


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
    last_err = None
    for attempt in range(6):
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
            # 429: троттлинг со стороны nginx/ОРД — делаем backoff и повторяем.
            if e.code == 429 and attempt < 5:
                retry_after = None
                try:
                    retry_after = e.headers.get('Retry-After')
                except Exception:
                    retry_after = None
                try:
                    ra = float(retry_after) if retry_after else None
                except Exception:
                    ra = None
                time.sleep(ra if ra is not None else (1.5 + attempt * 1.7))
                continue

            raw = e.read().decode('utf-8', errors='replace') if e.fp else ''
            parsed = None
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                parsed = {'_raw': raw}
            raise OrdVkApiError(e.code, raw, parsed) from e
        except urllib.error.URLError as e:
            # Временами nginx ОРД рвёт соединение (Errno 104). Делаем ретраи.
            last_err = e
            msg = str(e.reason or e)
            if attempt < 5 and ('104' in msg or 'reset' in msg.lower()):
                time.sleep(0.6 + attempt * 0.8)
                continue
            raise OrdVkApiError(0, msg, {'error': msg}) from e
        except ConnectionResetError as e:
            last_err = e
            if attempt < 5:
                time.sleep(0.6 + attempt * 0.8)
                continue
            raise OrdVkApiError(0, str(e), {'error': str(e)}) from e
    raise OrdVkApiError(0, str(last_err or 'network error'), {'error': str(last_err or 'network error')})


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


def _parse_external_id_items(data: Any) -> list[str]:
    """Ответ GET /v1/person|contract|pad: обычно {\"items\": [\"id\", ...]}."""
    if not isinstance(data, dict):
        return []
    raw = data.get('items')
    if raw is None:
        raw = data.get('external_ids')
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
        elif isinstance(x, dict):
            eid = (x.get('external_id') or x.get('id') or '').strip()
            if eid:
                out.append(eid)
    return out


def list_v1_entity_external_ids(
    bearer: str,
    entity: str,
    *,
    limit: int = 300,
    offset: int = 0,
    use_sandbox: bool = False,
) -> list[str]:
    """
    GET /v1/person | /v1/contract | /v1/pad — списки внешних id из кабинета ОРД.
    См. Swagger ОРД VK и darkdarin/vk-ord-sdk.
    """
    ent = (entity or '').strip().lower().rstrip('/')
    if ent not in ('person', 'contract', 'pad'):
        raise ValueError('entity must be person, contract or pad')
    q: dict[str, Any] = {'limit': max(1, min(int(limit), 1000))}
    if offset:
        q['offset'] = max(0, int(offset))
    path = f'/v1/{ent}?{urlencode(q)}'
    status, data = ord_request(bearer, 'GET', path, use_sandbox=use_sandbox)
    if status != 200:
        raise OrdVkApiError(status, json.dumps(data, ensure_ascii=False), data if isinstance(data, dict) else {})
    return _parse_external_id_items(data)


def get_v1_entity_json(
    bearer: str,
    entity: str,
    external_id: str,
    *,
    use_sandbox: bool = False,
) -> dict:
    """GET /v1/person/{id} | /v1/contract/{id} | /v1/pad/{id} — карточка объекта."""
    ent = (entity or '').strip().lower()
    eid = quote(str(external_id).strip(), safe='')
    if ent not in ('person', 'contract', 'pad') or not eid:
        raise ValueError('bad entity or external_id')
    path = f'/v1/{ent}/{eid}'
    status, data = ord_request(bearer, 'GET', path, use_sandbox=use_sandbox)
    if status != 200:
        raise OrdVkApiError(status, json.dumps(data, ensure_ascii=False), data if isinstance(data, dict) else {})
    return data if isinstance(data, dict) else {}


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

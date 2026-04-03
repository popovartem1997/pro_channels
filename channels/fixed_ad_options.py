"""
Две стандартные доп. услуги для рекламы: топ 1 ч и закреп на 24 ч.
Владелец включает галочкой и задаёт цену в настройках канала.
"""
from decimal import Decimal

from .models import Channel, ChannelAdAddon

CODE_TOP_1H = 'top_1h'
CODE_PIN_24H = 'pin_24h'


def get_fixed_ad_options_state(channel: Channel) -> dict:
    top = ChannelAdAddon.objects.filter(channel=channel, code__iexact=CODE_TOP_1H).first()
    pin = ChannelAdAddon.objects.filter(channel=channel, code__iexact=CODE_PIN_24H).first()
    return {
        'top_enabled': bool(top and top.is_active),
        'top_price': top.price if top else Decimal('0'),
        'pin_enabled': bool(pin and pin.is_active),
        'pin_price': pin.price if pin else Decimal('0'),
    }


def sync_fixed_ad_options(
    channel: Channel,
    *,
    top_enabled: bool,
    top_price: Decimal,
    pin_enabled: bool,
    pin_price: Decimal,
) -> None:
    """Создаёт/обновляет ровно две строки ChannelAdAddon с нормализованными кодами."""

    def upsert(code_lc: str, **values):
        row = ChannelAdAddon.objects.filter(channel=channel, code__iexact=code_lc).first()
        if row:
            for k, v in values.items():
                setattr(row, k, v)
            row.code = code_lc
            row.save()
        else:
            ChannelAdAddon.objects.create(channel=channel, code=code_lc, **values)

    upsert(
        CODE_TOP_1H,
        title='Топ публикации 1 час',
        addon_kind=ChannelAdAddon.ADDON_KIND_TOP_BLOCK,
        price=top_price,
        block_hours=1,
        max_pin_hours=72,
        top_duration_minutes=0,
        is_active=top_enabled,
    )
    upsert(
        CODE_PIN_24H,
        title='Закрепление поста на 24 часа',
        addon_kind=ChannelAdAddon.ADDON_KIND_CUSTOM,
        price=pin_price,
        block_hours=None,
        max_pin_hours=72,
        top_duration_minutes=0,
        is_active=pin_enabled,
    )

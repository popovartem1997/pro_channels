"""
Отображение каналов размещения в заявках: без дублей по логической группе (ChannelGroup).
"""


def channels_for_placement_display(channels_relation):
    """
    Один ряд на группу: если в M2M заказа попали TG и VK одного паблика (общий channel_group),
    в интерфейсе показываем одну площадку (канал с минимальным pk в группе).
    Каналы без группы выводятся все.
    """
    qs = channels_relation.all().select_related('channel_group').order_by('pk')
    by_group = {}
    ungrouped = []
    for ch in qs:
        gid = ch.channel_group_id
        if gid is None:
            ungrouped.append(ch)
        elif gid not in by_group:
            by_group[gid] = ch
    return ungrouped + list(by_group.values())

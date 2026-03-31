"""
Статистика по каналам и постам.
ChannelStat — снимок статистики канала в конкретный момент.
PostStat — статистика конкретного поста.
"""
from django.db import models


class ChannelStat(models.Model):
    """Снимок статистики канала (сохраняется при каждой синхронизации)."""
    channel = models.ForeignKey('channels.Channel', on_delete=models.CASCADE, related_name='stats', verbose_name='Канал')
    date = models.DateField(verbose_name='Дата', db_index=True)
    subscribers = models.PositiveIntegerField(default=0, verbose_name='Подписчиков')
    views = models.PositiveIntegerField(default=0, verbose_name='Просмотров за день')
    er = models.FloatField(default=0.0, verbose_name='ER (вовлечённость %)')
    posts_count = models.PositiveIntegerField(default=0, verbose_name='Постов за день')

    class Meta:
        verbose_name = 'Статистика канала'
        verbose_name_plural = 'Статистика каналов'
        unique_together = ('channel', 'date')
        ordering = ['-date']

    def __str__(self):
        return f'{self.channel.name} — {self.date}'


class PostStat(models.Model):
    """Статистика одного опубликованного поста."""
    post = models.ForeignKey('content.Post', on_delete=models.CASCADE, related_name='stats', verbose_name='Пост')
    channel = models.ForeignKey('channels.Channel', on_delete=models.CASCADE, related_name='post_stats', verbose_name='Канал')
    views = models.PositiveIntegerField(default=0, verbose_name='Просмотров')
    reactions = models.PositiveIntegerField(default=0, verbose_name='Реакций')
    forwards = models.PositiveIntegerField(default=0, verbose_name='Репостов')
    comments = models.PositiveIntegerField(default=0, verbose_name='Комментариев')
    synced_at = models.DateTimeField(auto_now=True, verbose_name='Обновлено')

    class Meta:
        verbose_name = 'Статистика поста'
        verbose_name_plural = 'Статистика постов'
        unique_together = ('post', 'channel')

    @property
    def er(self):
        """Engagement rate: (реакции + комментарии) / просмотры * 100."""
        if self.views == 0:
            return 0.0
        return round((self.reactions + self.comments) / self.views * 100, 2)

    def __str__(self):
        return f'Пост #{self.post.pk} в {self.channel.name}: {self.views} просм.'


class PostStatSnapshot(models.Model):
    """История изменения статистики поста (для графиков динамики)."""
    post = models.ForeignKey('content.Post', on_delete=models.CASCADE, related_name='stat_snapshots', verbose_name='Пост')
    channel = models.ForeignKey('channels.Channel', on_delete=models.CASCADE, related_name='post_stat_snapshots', verbose_name='Канал')
    views = models.PositiveIntegerField(default=0)
    reactions = models.PositiveIntegerField(default=0)
    forwards = models.PositiveIntegerField(default=0)
    comments = models.PositiveIntegerField(default=0)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Снимок статистики поста'
        verbose_name_plural = 'Снимки статистики постов'
        indexes = [models.Index(fields=['post', 'recorded_at'])]
        ordering = ['-recorded_at']


class MarketingReport(models.Model):
    """Маркетинговый отчёт канала за период (CPM, ER, охват, прирост)."""
    channel = models.ForeignKey('channels.Channel', on_delete=models.CASCADE, related_name='marketing_reports', verbose_name='Канал')
    period_start = models.DateField('Начало периода')
    period_end = models.DateField('Конец периода')
    avg_reach = models.FloatField('Средний охват на пост', default=0.0)
    avg_er = models.FloatField('Средний ER (%)', default=0.0)
    total_views = models.PositiveIntegerField('Всего просмотров', default=0)
    total_posts = models.PositiveIntegerField('Постов за период', default=0)
    total_reactions = models.PositiveIntegerField('Всего реакций', default=0)
    subscribers_start = models.PositiveIntegerField('Подписчиков в начале', default=0)
    subscribers_end = models.PositiveIntegerField('Подписчиков в конце', default=0)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Маркетинговый отчёт'
        verbose_name_plural = 'Маркетинговые отчёты'
        unique_together = ('channel', 'period_start', 'period_end')

    @property
    def subscribers_growth(self):
        return self.subscribers_end - self.subscribers_start

    def __str__(self):
        return f'{self.channel.name}: {self.period_start} — {self.period_end}'

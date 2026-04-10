from django import forms

from .models import GlobalApiKeys


class GlobalApiKeysForm(forms.ModelForm):
    # Secret inputs (raw)
    deepseek_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        label='DeepSeek API key',
    )
    tbank_terminal_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True), label='TBANK_TERMINAL_KEY')
    tbank_secret_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True), label='TBANK_SECRET_KEY')
    vk_ord_access_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True),
        label='Bearer-токен ОРД VK',
    )
    telegram_api_hash = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True), label='TELEGRAM_API_HASH')
    vk_parse_access_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True), label='VK_PARSE_ACCESS_TOKEN')
    class Meta:
        model = GlobalApiKeys
        fields = [
            'ai_rewrite_enabled',
            'vk_ord_cabinet_id',
            'vk_ord_contract_external_id',
            'vk_ord_pad_external_id',
            'vk_ord_operator_person_external_id',
            'vk_ord_use_sandbox',
            'vk_ord_contract_sum_from_campaign_total',
            'vk_ord_contract_amount_fixed',
            'telegram_api_id',
            'telegram_bot_proxy_url',
            'parse_media_retention_days',
            'parse_media_disk_quota_bytes',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pm = self.fields.get('parse_media_retention_days')
        if pm is not None:
            pm.widget.attrs.setdefault('min', 1)
            pm.widget.attrs.setdefault('max', 365)
            pm.widget.attrs.setdefault('step', 1)
        pq = self.fields.get('parse_media_disk_quota_bytes')
        if pq is not None:
            pq.widget.attrs.setdefault('min', 0)
            pq.widget.attrs.setdefault('step', 1)
        # Bootstrap classes
        for name, field in self.fields.items():
            widget = field.widget
            if hasattr(widget, 'attrs'):
                existing = widget.attrs.get('class', '')
                if isinstance(widget, forms.CheckboxInput):
                    widget.attrs['class'] = (existing + ' form-check-input').strip()
                else:
                    widget.attrs['class'] = (existing + ' form-control').strip()

    def save(self, commit=True):
        obj: GlobalApiKeys = super().save(commit=False)
        # Update encrypted fields only if provided (non-empty) to avoid accidental wipe.
        def _set_if_provided(field_name, setter):
            val = (self.cleaned_data.get(field_name) or '').strip()
            if val:
                setter(val)

        _set_if_provided('deepseek_api_key', obj.set_deepseek_api_key)
        _set_if_provided('tbank_terminal_key', obj.set_tbank_terminal_key)
        _set_if_provided('tbank_secret_key', obj.set_tbank_secret_key)
        _set_if_provided('vk_ord_access_token', obj.set_vk_ord_access_token)
        _set_if_provided('telegram_api_hash', obj.set_telegram_api_hash)
        _set_if_provided('vk_parse_access_token', obj.set_vk_parse_access_token)
        if commit:
            obj.save()
        return obj


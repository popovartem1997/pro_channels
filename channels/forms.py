from django import forms
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet

from .models import Channel, ChannelAdAddon


class ChannelAdAddonOwnerBaseFormSet(BaseInlineFormSet):
    """Пустая дополнительная строка в formset не требует заполнения."""

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        if not getattr(form.instance, 'pk', None):
            form.empty_permitted = True
        return form


class ChannelAdAddonOwnerForm(forms.ModelForm):
    """Доп. услуги рекламы в настройках канала (без поля top_duration_minutes — редко нужно владельцу)."""

    class Meta:
        model = ChannelAdAddon
        fields = [
            'code',
            'title',
            'addon_kind',
            'price',
            'block_hours',
            'max_pin_hours',
            'is_active',
        ]
        widgets = {
            'code': forms.TextInput(
                attrs={
                    'class': 'form-control form-control-sm',
                    'placeholder': 'top_1h, pin…',
                }
            ),
            'title': forms.TextInput(
                attrs={
                    'class': 'form-control form-control-sm',
                    'placeholder': 'Топ 1 час',
                }
            ),
            'addon_kind': forms.Select(attrs={'class': 'form-select form-select-sm'}),
            'price': forms.TextInput(
                attrs={
                    'class': 'form-control form-control-sm font-monospace',
                    'placeholder': '0',
                    'inputmode': 'decimal',
                }
            ),
            'block_hours': forms.NumberInput(
                attrs={'class': 'form-control form-control-sm', 'min': 1, 'max': 168, 'placeholder': '—'}
            ),
            'max_pin_hours': forms.NumberInput(
                attrs={'class': 'form-control form-control-sm', 'min': 1, 'max': 168}
            ),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_code(self):
        code = (self.cleaned_data.get('code') or '').strip()
        if not code:
            raise forms.ValidationError('Укажите короткий код (латиница, цифры, подчёркивание).')
        return code[:32]


ChannelAdAddonOwnerFormSet = inlineformset_factory(
    Channel,
    ChannelAdAddon,
    form=ChannelAdAddonOwnerForm,
    formset=ChannelAdAddonOwnerBaseFormSet,
    extra=1,
    can_delete=True,
    max_num=40,
    min_num=0,
    validate_min=False,
)

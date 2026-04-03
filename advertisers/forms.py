from django import forms

from .models import Advertiser


class AdvertiserRequisitesForm(forms.ModelForm):
    """Юр. реквизиты рекламодателя (договор, ВК ОРД). Редактируются отдельно от полей User в «Профиль»."""

    class Meta:
        model = Advertiser
        fields = [
            'ord_model_scheme',
            'company_name',
            'inn',
            'kpp',
            'ogrn',
            'legal_address',
            'actual_address',
            'contact_person',
            'contact_phone',
            'bank_name',
            'bank_account',
            'bank_bik',
            'bank_corr_account',
        ]
        labels = {
            'company_name': 'Название компании, ИП или физлица',
        }
        help_texts = {
            'company_name': 'Юрлицо — полное наименование с ОПФ. ИП или физлицо — ФИО как в договоре.',
        }
        widgets = {
            'ord_model_scheme': forms.Select(attrs={'class': 'form-select'}),
            'company_name': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'placeholder': 'ООО «…», ИП Иванов И.И. или ФИО физлица',
                }
            ),
            'inn': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '12', 'inputmode': 'numeric'}),
            'kpp': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '9'}),
            'ogrn': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '15'}),
            'legal_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'actual_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'contact_person': forms.TextInput(attrs={'class': 'form-control'}),
            'contact_phone': forms.TextInput(attrs={'class': 'form-control'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control'}),
            'bank_account': forms.TextInput(attrs={'class': 'form-control'}),
            'bank_bik': forms.TextInput(attrs={'class': 'form-control'}),
            'bank_corr_account': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_inn(self):
        inn = (self.cleaned_data.get('inn') or '').strip()
        if not inn.isdigit() or len(inn) not in (10, 12):
            raise forms.ValidationError('ИНН: 10 цифр (юрлицо) или 12 цифр (ИП).')
        return inn

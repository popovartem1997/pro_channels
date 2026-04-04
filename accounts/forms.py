import re

from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .models import User


class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email'})
    )
    first_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Имя'})
    )
    phone = forms.CharField(
        max_length=20, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+7 (999) 999-99-99'})
    )
    company = forms.CharField(
        label='Название компании, ИП или физлица',
        max_length=255,
        required=True,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control',
                'placeholder': 'ООО «…», ИП Иванов И.И. или ФИО физлица',
            }
        ),
        help_text='Юрлицо — полное наименование с ОПФ. ИП или физлицо — ФИО как в договоре.',
    )

    class Meta:
        model = User
        fields = ['first_name', 'email', 'phone', 'company', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Пароль'})
        self.fields['password2'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Повторите пароль'})
        self.fields['company'].widget.attrs.setdefault(
            'aria-label', self.fields['company'].label
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.username = self.cleaned_data['email']  # username = email
        user.phone = self.cleaned_data.get('phone', '')
        user.company = self.cleaned_data.get('company', '')
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label='Email или логин',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Email или логин (например: admin)',
            'autofocus': True,
            'autocomplete': 'username',
        })
    )
    password = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Пароль', 'autocomplete': 'current-password'})
    )


class ProfileForm(forms.ModelForm):
    telegram_user_id = forms.CharField(
        required=False,
        label='Telegram user ID',
        widget=forms.TextInput(
            attrs={'class': 'form-control font-monospace', 'placeholder': 'Например 123456789', 'autocomplete': 'off'}
        ),
        help_text='Числовой ID в Telegram (например через @userinfobot). Нужен, если вы в списке «Кому слать модерацию» у бота предложки.',
    )
    max_user_id = forms.CharField(
        required=False,
        label='MAX user ID',
        widget=forms.TextInput(
            attrs={'class': 'form-control font-monospace', 'placeholder': 'user_id в MAX', 'autocomplete': 'off'}
        ),
        help_text='Идентификатор пользователя MAX для личных уведомлений о предложках.',
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone', 'company', 'avatar']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'company': forms.TextInput(attrs={'class': 'form-control'}),
            'avatar': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['company'].label = 'Название компании, ИП или физлица'
        if self.instance.pk and self.instance.telegram_user_id is not None:
            self.fields['telegram_user_id'].initial = str(self.instance.telegram_user_id)
        if self.instance.pk:
            self.fields['max_user_id'].initial = self.instance.max_user_id or ''

    def clean_telegram_user_id(self):
        v = (self.cleaned_data.get('telegram_user_id') or '').strip()
        if not v:
            return None
        if not re.fullmatch(r'-?\d+', v):
            raise forms.ValidationError('Укажите числовой Telegram ID.')
        return int(v)

    def clean_max_user_id(self):
        return (self.cleaned_data.get('max_user_id') or '').strip()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.telegram_user_id = self.cleaned_data.get('telegram_user_id')
        user.max_user_id = self.cleaned_data.get('max_user_id') or ''
        if commit:
            user.save()
            from managers.models import sync_team_member_platform_ids_from_user

            sync_team_member_platform_ids_from_user(user)
        return user

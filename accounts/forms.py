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
        max_length=255, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Название компании или ИП'})
    )

    class Meta:
        model = User
        fields = ['first_name', 'email', 'phone', 'company', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Пароль'})
        self.fields['password2'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Повторите пароль'})

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

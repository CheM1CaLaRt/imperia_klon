from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.contrib.auth import get_user_model
import re
from datetime import date

from .models import Profile

User = get_user_model()

PHONE_RE = re.compile(r"^\+?\d{7,15}$")
TG_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]  # логин НЕ редактируем
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "Имя"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Фамилия"}),
            "email": forms.EmailInput(attrs={"placeholder": "you@example.com"}),
        }

class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["avatar", "phone", "whatsapp", "telegram", "vk", "birth_date"]
        widgets = {
            "phone": forms.TextInput(attrs={
                "placeholder": "+79990001122",
                "pattern": r"^\+?\d{7,15}$",
                "title": "7–15 цифр, допустим + в начале",
            }),
            "whatsapp": forms.TextInput(attrs={
                "placeholder": "+79990001122 или 79990001122",
                "pattern": r"^\+?\d{7,15}$",
                "title": "7–15 цифр, допустим + в начале",
            }),
            "telegram": forms.TextInput(attrs={
                "placeholder": "@username",
                "pattern": r"^@?[A-Za-z0-9_]{5,32}$",
                "title": "5–32 символа: латиница/цифры/_; можно с @",
            }),
            "vk": forms.URLInput(attrs={"placeholder": "https://vk.com/username"}),
            "birth_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_phone(self):
        v = (self.cleaned_data.get("phone") or "").strip().replace(" ", "")
        if not v:
            return v
        if not PHONE_RE.fullmatch(v):
            raise ValidationError("Телефон: 7–15 цифр, можно + в начале.")
        return v

    def clean_whatsapp(self):
        v = (self.cleaned_data.get("whatsapp") or "").strip().replace(" ", "")
        if not v:
            return v
        if not PHONE_RE.fullmatch(v):
            raise ValidationError("WhatsApp: 7–15 цифр, можно + в начале.")
        return v

    def clean_telegram(self):
        v = (self.cleaned_data.get("telegram") or "").strip()
        if not v:
            return v
        if v.startswith("https://t.me/"):
            v = v[len("https://t.me/"):]
        v = v.lstrip("@")
        if not TG_RE.fullmatch(v):
            raise ValidationError("Telegram: 5–32 символов (латиница, цифры, _).")
        return "@" + v

    def clean_vk(self):
        v = (self.cleaned_data.get("vk") or "").strip()
        if not v:
            return v
        URLValidator()(v)
        if "vk.com" not in v:
            raise ValidationError("Ссылка должна вести на vk.com.")
        return v

    def clean_birth_date(self):
        bd = self.cleaned_data.get("birth_date")
        if not bd:
            return bd
        today = date.today()
        if bd > today:
            raise ValidationError("Дата рождения не может быть в будущем.")
        if bd.year < today.year - 120:
            raise ValidationError("Слишком ранняя дата рождения.")
        return bd

# core/forms_employees.py
from django import forms
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import Profile


class EmployeeForm(forms.ModelForm):
    """Форма для создания и редактирования сотрудника"""
    
    # Поля пользователя
    username = forms.CharField(
        label="Логин",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            "class": "input w-full",
            "placeholder": "Введите логин",
            "autocomplete": "username"
        }),
        help_text="Уникальный логин для входа в систему"
    )
    first_name = forms.CharField(
        label="Имя",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            "class": "input w-full",
            "placeholder": "Введите имя"
        })
    )
    last_name = forms.CharField(
        label="Фамилия",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input w-full",
            "placeholder": "Введите фамилию"
        })
    )
    email = forms.EmailField(
        label="Email",
        required=False,
        widget=forms.EmailInput(attrs={
            "class": "input w-full",
            "placeholder": "email@example.com"
        })
    )
    password = forms.CharField(
        label="Пароль",
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "input w-full",
            "placeholder": "Оставьте пустым, чтобы не менять",
            "autocomplete": "new-password"
        }),
        help_text="Оставьте пустым при редактировании, чтобы не менять пароль"
    )
    is_active = forms.BooleanField(
        label="Активен",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            "class": "checkbox"
        }),
        help_text="Неактивные пользователи не могут войти в систему"
    )
    
    # Роль (группа)
    role = forms.ModelChoiceField(
        label="Роль",
        queryset=Group.objects.all(),
        required=True,
        widget=forms.Select(attrs={
            "class": "input w-full"
        }),
        help_text="Выберите роль сотрудника в системе"
    )
    
    # Поля профиля
    phone = forms.CharField(
        label="Телефон",
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input w-full",
            "placeholder": "+7 (999) 123-45-67"
        })
    )
    whatsapp = forms.CharField(
        label="WhatsApp",
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input w-full",
            "placeholder": "+7 (999) 123-45-67 или @username"
        })
    )
    telegram = forms.CharField(
        label="Telegram",
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input w-full",
            "placeholder": "@username"
        })
    )
    vk = forms.URLField(
        label="VK",
        max_length=255,
        required=False,
        widget=forms.URLInput(attrs={
            "class": "input w-full",
            "placeholder": "https://vk.com/username"
        })
    )
    birth_date = forms.DateField(
        label="Дата рождения",
        required=False,
        widget=forms.DateInput(attrs={
            "class": "input w-full",
            "type": "date"
        })
    )
    
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]
    
    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        super().__init__(*args, **kwargs)
        
        # Настраиваем queryset для ролей (только существующие группы)
        self.fields["role"].queryset = Group.objects.all().order_by("name")
        
        # Если редактируем существующего пользователя
        if instance and instance.pk:
            # Заполняем поля профиля
            try:
                profile = instance.profile
                self.fields["phone"].initial = profile.phone
                self.fields["whatsapp"].initial = profile.whatsapp
                self.fields["telegram"].initial = profile.telegram
                self.fields["vk"].initial = profile.vk
                self.fields["birth_date"].initial = profile.birth_date
            except Profile.DoesNotExist:
                pass
            
            # Устанавливаем текущую роль
            user_groups = instance.groups.all()
            if user_groups.exists():
                self.fields["role"].initial = user_groups.first()
            
            # Пароль не обязателен при редактировании
            self.fields["password"].required = False
        else:
            # При создании пароль обязателен
            self.fields["password"].required = True
    
    def clean_username(self):
        username = self.cleaned_data.get("username")
        if not username:
            return username
        
        # Проверяем уникальность логина
        user = User.objects.filter(username=username)
        if self.instance and self.instance.pk:
            user = user.exclude(pk=self.instance.pk)
        
        if user.exists():
            raise ValidationError("Пользователь с таким логином уже существует.")
        
        return username
    
    def clean_password(self):
        password = self.cleaned_data.get("password")
        
        # При создании пароль обязателен
        if not self.instance or not self.instance.pk:
            if not password:
                raise ValidationError("Пароль обязателен при создании пользователя.")
            # Валидируем пароль
            try:
                validate_password(password)
            except ValidationError as e:
                raise ValidationError(e.messages)
        
        # При редактировании пароль опционален, но если указан - валидируем
        elif password:
            try:
                validate_password(password)
            except ValidationError as e:
                raise ValidationError(e.messages)
        
        return password
    
    def save(self, commit=True):
        user = super().save(commit=False)
        
        # Устанавливаем пароль, если указан
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        
        if commit:
            user.save()
            
            # Обновляем группы пользователя
            role = self.cleaned_data.get("role")
            if role:
                user.groups.clear()
                user.groups.add(role)
            
            # Сохраняем или создаем профиль
            profile, created = Profile.objects.get_or_create(user=user)
            profile.phone = self.cleaned_data.get("phone", "")
            profile.whatsapp = self.cleaned_data.get("whatsapp", "")
            profile.telegram = self.cleaned_data.get("telegram", "")
            profile.vk = self.cleaned_data.get("vk", "")
            profile.birth_date = self.cleaned_data.get("birth_date")
            profile.save()
        
        return user


# core/forms_companies.py
from django import forms
from .models import Company, CompanyAddress


class CompanyForm(forms.ModelForm):
    """Форма для создания и редактирования компании"""
    
    class Meta:
        model = Company
        fields = [
            "name", "full_name", "inn", "kpp", "ogrn", "address",
            "phone", "email",
            "bank_name", "bank_bik", "bank_account", "bank_corr_account",
            "director_name", "director_position",
            "accountant_name", "accountant_position",
            "is_active"
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": 'ООО "Ваша компания"'
            }),
            "full_name": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": 'Общество с ограниченной ответственностью "Ваша компания"'
            }),
            "inn": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "1234567890",
                "pattern": r"\d{10,12}"
            }),
            "kpp": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "123456789",
                "pattern": r"\d{9}"
            }),
            "ogrn": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "1234567890123",
                "pattern": r"\d{13,15}"
            }),
            "address": forms.Textarea(attrs={
                "class": "input w-full",
                "rows": 3,
                "placeholder": "г. Москва, ул. Примерная, д. 1"
            }),
            "phone": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "+7 (495) 123-45-67"
            }),
            "email": forms.EmailInput(attrs={
                "class": "input w-full",
                "placeholder": "info@example.com"
            }),
            "bank_name": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": 'ПАО "Банк"'
            }),
            "bank_bik": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "123456789",
                "pattern": r"\d{9}"
            }),
            "bank_account": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "40702810123456789012",
                "pattern": r"\d{20}"
            }),
            "bank_corr_account": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "30101810100000000593",
                "pattern": r"\d{20}"
            }),
            "director_name": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "Иванов Иван Иванович"
            }),
            "director_position": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "Генеральный директор"
            }),
            "accountant_name": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "Петрова Мария Сергеевна"
            }),
            "accountant_position": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "Главный бухгалтер"
            }),
            "is_active": forms.CheckboxInput(attrs={
                "class": "checkbox"
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Делаем обязательными ключевые поля
        self.fields["name"].required = True
        self.fields["full_name"].required = True
        self.fields["inn"].required = True
        self.fields["address"].required = True


class CompanyAddressForm(forms.ModelForm):
    """Форма для адреса компании"""
    class Meta:
        model = CompanyAddress
        fields = ["address_type", "address", "is_default"]
        widgets = {
            "address_type": forms.Select(attrs={
                "class": "input w-full",
                "style": "width:100%;padding:10px 14px;font-size:14px"
            }),
            "address": forms.TextInput(attrs={
                "class": "input w-full",
                "placeholder": "Введите адрес",
                "style": "width:100%;padding:10px 14px;font-size:14px"
            }),
            "is_default": forms.CheckboxInput(attrs={
                "class": "checkbox",
                "style": "width:18px;height:18px;cursor:pointer"
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["address"].required = False


CompanyAddressFormSet = forms.inlineformset_factory(
    Company,
    CompanyAddress,
    form=CompanyAddressForm,
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
)


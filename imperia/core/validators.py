from django.core.exceptions import ValidationError

def validate_inn(value: str):
    s = "".join(filter(str.isdigit, value or ""))
    if len(s) == 10:
        coeff = [2,4,10,3,5,9,4,6,8]
        d = sum(int(s[i])*coeff[i] for i in range(9)) % 11 % 10
        if d != int(s[9]): raise ValidationError("Неверная контрольная сумма ИНН")
    elif len(s) == 12:
        c1 = [7,2,4,10,3,5,9,4,6,8,0]
        c2 = [3,7,2,4,10,3,5,9,4,6,8,0]
        d1 = sum(int(s[i])*c1[i] for i in range(11)) % 11 % 10
        d2 = sum(int(s[i])*c2[i] for i in range(12)) % 11 % 10
        if d1 != int(s[10]) or d2 != int(s[11]): raise ValidationError("Неверная контрольная сумма ИНН")
    else:
        raise ValidationError("ИНН должен содержать 10 (ЮЛ) или 12 (ИП) цифр")

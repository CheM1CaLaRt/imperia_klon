from django.db import models
from django.contrib.auth.models import User

def avatar_upload_to(instance, filename):
    return f"avatars/user_{instance.user_id}/{filename}"

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(upload_to=avatar_upload_to, blank=True, null=True)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    whatsapp = models.CharField("WhatsApp (номер или @)", max_length=64, blank=True)
    telegram = models.CharField("Telegram (@username)", max_length=64, blank=True)
    vk = models.URLField("VK (ссылка)", max_length=255, blank=True)

    birth_date = models.DateField("Дата рождения", blank=True, null=True)  # ← добавили

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"

    def __str__(self):
        return f"Профиль {self.user.username}"

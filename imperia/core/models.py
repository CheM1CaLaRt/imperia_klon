from django.db import models
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db.models.signals import pre_save, post_delete
from django.dispatch import receiver

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

@receiver(pre_save, sender=Profile)
def delete_old_avatar_on_change(sender, instance: Profile, **kwargs):
    """
    Если у пользователя уже был аватар и он заменяется новым (или очищается),
    удаляем старый файл из хранилища.
    """
    if not instance.pk:
        return  # новый профиль — старого файла нет
    try:
        old_avatar = sender.objects.get(pk=instance.pk).avatar
    except sender.DoesNotExist:
        return

    new_avatar = instance.avatar

    # если старый существует и он другой (или аватар очищается)
    if old_avatar and (not new_avatar or old_avatar.name != new_avatar.name):
        if old_avatar.name and default_storage.exists(old_avatar.name):
            default_storage.delete(old_avatar.name)

@receiver(post_delete, sender=Profile)
def delete_avatar_file_on_profile_delete(sender, instance: Profile, **kwargs):
    """При удалении профиля удаляем файл аватара."""
    if instance.avatar and instance.avatar.name and default_storage.exists(instance.avatar.name):
        default_storage.delete(instance.avatar.name)
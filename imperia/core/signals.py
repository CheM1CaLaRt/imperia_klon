# core/signals.py
import os
from django.db.models.signals import pre_save, post_delete, post_save
from django.dispatch import receiver
from .models import Profile
from django.core.files.storage import default_storage
from PIL import Image

def _remove_file(f):
    """Безопасно удалить файл на диске, если он существует."""
    if f and hasattr(f, "path"):
        try:
            if os.path.isfile(f.path):
                os.remove(f.path)
        except Exception:
            # молча игнорируем (например, если файл уже удалён)
            pass

@receiver(pre_save, sender=Profile)
def replace_avatar_cleanup(sender, instance: Profile, **kwargs):
    """Перед сохранением профиля — если аватар меняется, удалить старый файл."""
    if not instance.pk:
        return
    try:
        old = sender.objects.only("avatar").get(pk=instance.pk).avatar
    except sender.DoesNotExist:
        return
    new = instance.avatar
    if old and old != new:
        _remove_file(old)

@receiver(post_delete, sender=Profile)
def delete_avatar_file(sender, instance: Profile, **kwargs):
    """При удалении профиля — удалить файл аватара."""
    _remove_file(instance.avatar)


MAX_SIDE = 512  # максимум по стороне
JPEG_QUALITY = 85

@receiver(post_save, sender=Profile)
def resize_avatar_if_needed(sender, instance: Profile, created, **kwargs):
    """
    После сохранения: если загружен аватар, уменьшаем до 512px по длинной стороне
    и перекодируем в JPEG (качество ~85). Прозрачность потеряется — для аватаров норм.
    """
    avatar = instance.avatar
    if not avatar:
        return
    try:
        path = avatar.path  # локальный путь файлового хранилища
    except Exception:
        # например, кастомное storage без .path — тогда пропускаем
        return

    try:
        with Image.open(path) as im:
            # уже маленький — ничего не делаем
            if max(im.size) <= MAX_SIDE and avatar.size <= 600 * 1024:
                return

            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            # перезаписываем файл на диске (тот же путь)
            im.save(path, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception:
        # какие-то экзотические форматы/ошибки PIL — просто пропускаем
        pass

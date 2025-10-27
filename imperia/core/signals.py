# core/signals.py
import os
from django.db.models.signals import pre_save, post_delete, post_save
from django.dispatch import receiver
from .models import Profile
from django.core.files.storage import default_storage
from PIL import Image
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver
from .models import CounterpartyDocument

# === [МОИ ДОБАВЛЕНИЯ: импорты для заявок] ===============================
# ВАЖНО: модели заявок находятся в отдельном модуле models_requests,
# поэтому импортируем их отсюда, а не из .models
from .models_requests import Request as RequestModel, RequestHistory as RequestHistoryModel
from django.utils import timezone
# =======================================================================


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


@receiver(post_delete, sender=CounterpartyDocument)
def delete_document_file_on_delete(sender, instance: CounterpartyDocument, **kwargs):
    """
    Удаляем файл с диска, когда запись документа удалена
    (в т.ч. через formset с галочкой «удалить» или каскадно при удалении контрагента).
    """
    if instance.file:
        # save=False — чтобы не пытаться сохранить модель заново
        instance.file.delete(save=False)

@receiver(pre_save, sender=CounterpartyDocument)
def delete_old_file_on_change(sender, instance: CounterpartyDocument, **kwargs):
    """
    Если заменили файл у существующей записи — удалить старый файл с диска.
    """
    if not instance.pk:
        return  # создаётся новая запись — нечего удалять
    try:
        old = CounterpartyDocument.objects.get(pk=instance.pk)
    except CounterpartyDocument.DoesNotExist:
        return
    if old.file and old.file != instance.file:
        old.file.delete(save=False)


# ======================== МОИ ДОБАВЛЕНИЯ ДЛЯ «ЗАЯВОК» =========================
# Номер заявки при создании: формат R-YYYYMMDD-0001
@receiver(pre_save, sender=RequestModel)
def set_request_number_on_create(sender, instance: RequestModel, **kwargs):
    # Присваиваем номер только при создании
    if instance._state.adding and not instance.number:
        today = timezone.localdate()
        prefix = f"R-{today:%Y%m%d}-"
        last = RequestModel.objects.filter(number__startswith=prefix).order_by("-number").first()
        seq = int(last.number.split("-")[-1]) + 1 if (last and last.number and last.number.startswith(prefix)) else 1
        instance.number = f"{prefix}{seq:04d}"

# Первая запись об истории — сразу после создания
@receiver(post_save, sender=RequestModel)
def add_request_history_on_create(sender, instance: RequestModel, created, **kwargs):
    if created:
        RequestHistoryModel.objects.create(
            request=instance,
            author=instance.initiator,
            to_status=instance.status,
            note="Создана",
        )
# ============================================================================


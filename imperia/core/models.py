from django.db import models
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db.models.signals import pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

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

class Supplier(models.Model):
    # Примеры: "samson", "1c", "other_vendor"
    code = models.SlugField(unique=True, max_length=50)
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name

class ImportBatch(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    source_name = models.CharField(max_length=200, help_text="Файл или URL")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    items_total = models.PositiveIntegerField(default=0)
    items_upserted = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.supplier.code} — {self.source_name} — {self.started_at:%Y-%m-%d %H:%M}"

class Product(models.Model):
    # уникальность: если есть barcode -> по нему глобально; иначе по (supplier, sku)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    sku = models.CharField(max_length=100, db_index=True)  # из Самсона: "sku"
    barcode = models.CharField(max_length=64, blank=True, null=True, unique=True)
    name = models.TextField()
    name_1c = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    description_ext = models.TextField(blank=True, default="")
    brand = models.CharField(max_length=200, blank=True, default="")
    manufacturer_country = models.CharField(max_length=200, blank=True, default="")  # Самсон: "manufacturer"
    vendor_code = models.CharField(max_length=200, blank=True, default="")
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    volume_m3 = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)

    # Габариты упаковки (см)
    pkg_height_cm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pkg_width_cm  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pkg_depth_cm  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Управление жизненным циклом
    is_active = models.BooleanField(default=True)
    last_import_batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.SET_NULL)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["supplier", "sku"], name="idx_supplier_sku"),
        ]
        constraints = [
            # Не даём дубликаты по (supplier, sku) когда barcode отсутствует/пуст
            models.UniqueConstraint(
                fields=["supplier", "sku"],
                name="uniq_supplier_sku_when_no_barcode",
                condition=models.Q(barcode__isnull=True) | models.Q(barcode__exact="")
            )
        ]

    def __str__(self):
        return self.name

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    url = models.URLField()
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

class ProductCertificate(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="certificates")
    issued_by = models.CharField(max_length=300, blank=True, default="")
    name = models.CharField(max_length=300, blank=True, default="")
    active_to = models.CharField(max_length=100, blank=True, default="")  # храним как строку (форматы разнятся)
    url = models.URLField()

class ProductPrice(models.Model):
    CONTRACT = "contract"
    INFILTRATION = "infiltration"
    OTHER = "other"
    TYPE_CHOICES = [
        (CONTRACT, "Contract"),
        (INFILTRATION, "Infiltration"),
        (OTHER, "Other"),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="prices")
    price_type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="RUB")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("product", "price_type")]
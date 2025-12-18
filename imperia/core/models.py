from django.db import models
from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.db.models.signals import pre_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db.models import Q
from django.core.validators import RegexValidator
from django.core.validators import URLValidator
from django.db import transaction





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


class ProductCategory(models.Model):
    """Иерархическая категория товаров"""
    name = models.CharField("Название", max_length=200)
    slug = models.SlugField("URL-адрес", max_length=200, unique=True, db_index=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Родительская категория"
    )
    description = models.TextField("Описание", blank=True, default="")
    icon = models.CharField("Иконка (эмодзи)", max_length=10, blank=True, default="📦")
    color = models.CharField("Цвет (hex)", max_length=7, blank=True, default="#667eea", help_text="Цвет для отображения категории")
    order = models.PositiveIntegerField("Порядок сортировки", default=0, db_index=True)
    is_active = models.BooleanField("Активна", default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Категория товара"
        verbose_name_plural = "Категории товаров"
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["parent", "is_active"]),
        ]

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} → {self.name}"
        return self.name
    
    def get_full_path(self):
        """Возвращает полный путь категории (родитель → категория)"""
        path = [self.name]
        current = self.parent
        while current:
            path.insert(0, current.name)
            current = current.parent
        return " → ".join(path)
    
    def get_all_children(self):
        """Возвращает все дочерние категории (рекурсивно)"""
        children = list(self.children.filter(is_active=True))
        for child in self.children.filter(is_active=True):
            children.extend(child.get_all_children())
        return children


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
    category = models.ForeignKey(
        "ProductCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="Категория"
    )
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
    pkg_width_cm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pkg_depth_cm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Управление жизненным циклом
    is_active = models.BooleanField(default=True)
    last_import_batch = models.ForeignKey(ImportBatch, null=True, blank=True, on_delete=models.SET_NULL)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["supplier", "sku"], name="idx_supplier_sku"),
            models.Index(fields=["category", "is_active"], name="idx_category_active"),
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


class Warehouse(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    address = models.CharField(max_length=255, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class StorageBin(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="bins")
    code = models.CharField(max_length=40)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Ячейка хранения"
        verbose_name_plural = "Ячейки хранения"
        unique_together = ("warehouse", "code")
        ordering = ["warehouse__name", "code"]

    def __str__(self):
        return f"{self.warehouse.code}:{self.code}"


class Inventory(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="inventory")
    bin = models.ForeignKey(StorageBin, on_delete=models.SET_NULL, null=True, blank=True, related_name="inventory")
    product = models.ForeignKey("core.Product", on_delete=models.CASCADE, related_name="inventory")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # 1) уникальность, когда bin НЕ NULL
            models.UniqueConstraint(
                fields=["warehouse", "product", "bin"],
                condition=Q(bin__isnull=False),
                name="uniq_inventory_row_not_null_bin",
            ),
            # 2) уникальность, когда bin = NULL (ровно одна строка на (warehouse, product))
            models.UniqueConstraint(
                fields=["warehouse", "product"],
                condition=Q(bin__isnull=True),
                name="uniq_inventory_row_null_bin",
            ),
            # 3) количество неотрицательное
            models.CheckConstraint(
                check=Q(quantity__gte=0),
                name="inventory_qty_nonneg",
            ),
        ]
        verbose_name = "Остаток"
        verbose_name_plural = "Остатки"
        unique_together = ("warehouse", "bin", "product")

    def __str__(self):
        place = self.bin.code if self.bin else "—"
        return f"{self.product} @ {self.warehouse.code}/{place}: {self.quantity}"


class StockMovement(models.Model):
    INBOUND = "IN"
    OUTBOUND = "OUT"
    MOVE = "MOVE"
    ADJUST = "ADJ"

    MOVEMENT_TYPES = [
        (INBOUND, "Поступление"),
        (OUTBOUND, "Списание/Отгрузка"),
        (MOVE, "Перемещение"),
        (ADJUST, "Корректировка"),
    ]

    timestamp = models.DateTimeField(default=timezone.now)
    movement_type = models.CharField(max_length=4, choices=MOVEMENT_TYPES)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE)
    bin_from = models.ForeignKey(StorageBin, on_delete=models.SET_NULL, null=True, blank=True, related_name="moves_from")
    bin_to = models.ForeignKey(StorageBin, on_delete=models.SET_NULL, null=True, blank=True, related_name="moves_to")
    product = models.ForeignKey("core.Product", on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Движение товара"
        verbose_name_plural = "Движения товара"
        ordering = ["-timestamp", "id"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.movement_type} {self.product} x{self.quantity}"


      # -------------------контрагенты-------------------

inn_validator = RegexValidator(
    regex=r"^\d{10}(\d{2})?$",  # 10 (юр.лица) или 12 (ИП)
    message="ИНН должен содержать 10 или 12 цифр"
)

class Counterparty(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    inn = models.CharField("ИНН", max_length=12, unique=True, validators=[inn_validator])
    kpp = models.CharField("КПП", max_length=9, blank=True)
    ogrn = models.CharField("ОГРН/ОГРНИП", max_length=15, blank=True)

    name = models.CharField("Наименование", max_length=512)
    full_name = models.CharField("Полное наименование", max_length=1024, blank=True)

    registration_country = models.CharField(
        "Страна регистрации", max_length=128, blank=True, default="РОССИЯ"
    )

    # Юр. адрес (как и было)
    address = models.CharField("Адрес", max_length=1024, blank=True)

    # 🔹 Новое: фактический адрес / адрес доставки
    actual_address = models.CharField(
        "Фактический адрес / адрес доставки", max_length=1024, blank=True
    )

    # 🔹 Новое: банковские реквизиты
    bank_name = models.CharField("Банк (наименование)", max_length=255, blank=True)
    bank_bik = models.CharField("БИК", max_length=20, blank=True)
    bank_account = models.CharField("Номер счёта", max_length=34, blank=True)

    website = models.URLField("Сайт", blank=True, null=True)

    # Менеджеры (как было)
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="managed_counterparties",
        blank=True,
        verbose_name="Закреплённые менеджеры",
        help_text="Выберите одного или нескольких менеджеров.",
    )

    # Сырой JSON с ЕГРЮЛ
    meta_json = models.JSONField("Данные из ЕГРЮЛ (сырые)", default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        permissions = [
            ("add_counterparty_by_inn", "Может добавлять контрагентов по ИНН"),
        ]

    def __str__(self):
        return f"{self.name} ({self.inn})"


class CounterpartyAddress(models.Model):
    """Фактический адрес / адрес доставки контрагента"""
    counterparty = models.ForeignKey(
        "Counterparty",
        on_delete=models.CASCADE,
        related_name="addresses",
        verbose_name="Контрагент",
    )
    address = models.CharField(
        "Фактический адрес / адрес доставки",
        max_length=1024,
        blank=False,
    )
    is_default = models.BooleanField(
        "Адрес по умолчанию",
        default=False,
        help_text="Использовать как основной адрес доставки",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "created_at"]
        verbose_name = "Адрес доставки"
        verbose_name_plural = "Адреса доставки"

    def __str__(self):
        return self.address[:50] + ("..." if len(self.address) > 50 else "")


class CounterpartyDocument(models.Model):
    counterparty = models.ForeignKey(
        "Counterparty",
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Контрагент",
    )
    title = models.CharField("Название", max_length=255, blank=True)
    file = models.FileField(
        "Файл",
        upload_to="counterparty_docs/%Y/%m/%d/",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or (self.file.name if self.file else "Документ")



class CounterpartyFinance(models.Model):
    counterparty = models.OneToOneField(Counterparty, on_delete=models.CASCADE, related_name="finance")
    data = models.JSONField(default=dict, blank=True)
    revenue_last = models.DecimalField("Выручка, последний год", max_digits=18, decimal_places=2, null=True, blank=True)
    profit_last  = models.DecimalField("Чистая прибыль, последний год", max_digits=18, decimal_places=2, null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

class CounterpartyContact(models.Model):
    counterparty = models.ForeignKey(
        Counterparty, on_delete=models.CASCADE, related_name="contacts", verbose_name="Контрагент"
    )
    full_name = models.CharField("ФИО", max_length=255)
    position = models.CharField("Должность", max_length=255, blank=True)
    email = models.EmailField("Email", blank=True)
    phone = models.CharField("Телефон", max_length=50, blank=True)
    mobile = models.CharField("Моб. телефон", max_length=50, blank=True)
    note = models.CharField("Комментарий", max_length=500, blank=True)
    birthday = models.DateField(null=True, blank=True, verbose_name="День рождения")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Контактное лицо"
        verbose_name_plural = "Контактные лица"

    def __str__(self):
        return f"{self.full_name} ({self.counterparty.name})"

class CounterpartyDeletionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        APPROVED = "approved", "Одобрено"
        REJECTED = "rejected", "Отклонено"

    counterparty = models.ForeignKey(
        "Counterparty",
        on_delete=models.CASCADE,
        related_name="deletion_requests",
        verbose_name="Контрагент",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="counterparty_delete_requests",
        verbose_name="Кем подано",
    )
    comment = models.TextField("Комментарий оператора", blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="counterparty_delete_reviews",
        verbose_name="Кем рассмотрено",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заявка на удаление контрагента"
        verbose_name_plural = "Заявки на удаление контрагентов"

    def __str__(self):
        return f"Удаление {self.counterparty} ({self.get_status_display()})"


class CounterpartyCreateRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает подтверждения"
        APPROVED = "approved", "Одобрено"
        REJECTED = "rejected", "Отклонено"

    # Данные будущего контрагента (копия основных полей)
    inn = models.CharField("ИНН", max_length=12, validators=[inn_validator], db_index=True)
    kpp = models.CharField("КПП", max_length=9, blank=True)
    ogrn = models.CharField("ОГРН/ОГРНИП", max_length=15, blank=True)

    name = models.CharField("Наименование", max_length=512)
    full_name = models.CharField("Полное наименование", max_length=1024, blank=True)

    registration_country = models.CharField("Страна регистрации", max_length=128, blank=True, default="РОССИЯ")
    address = models.CharField("Адрес", max_length=1024, blank=True)
    actual_address = models.CharField("Фактический адрес / адрес доставки", max_length=1024, blank=True)

    bank_name = models.CharField("Банк (наименование)", max_length=255, blank=True)
    bank_bik = models.CharField("БИК", max_length=20, blank=True)
    bank_account = models.CharField("Номер счёта", max_length=34, blank=True)

    website = models.URLField("Сайт", blank=True, null=True)

    # Кто создал (менеджер) — сразу проставляется автор заявки
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="counterparty_create_requests",
        verbose_name="Менеджер-инициатор",
    )

    # Ссылка на созданного контрагента (заполняется при approve)
    counterparty = models.ForeignKey(
        "Counterparty",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="create_requests",
        verbose_name="Созданный контрагент",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="counterparty_create_reviews",
        verbose_name="Кем рассмотрено",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_comment = models.TextField("Комментарий ревьюера", blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Заявка на добавление контрагента"
        verbose_name_plural = "Заявки на добавление контрагентов"
        constraints = [
            # Не допускаем дубликатов PENDING по одному ИНН
            models.UniqueConstraint(
                fields=["inn"],
                condition=Q(status="pending"),
                name="uniq_pending_counterparty_create_by_inn",
            )
        ]

    def __str__(self):
        return f"Заявка на контрагента {self.name} ({self.inn}) — {self.get_status_display()}"

    @transaction.atomic
    def approve(self, reviewer, comment: str | None = None) -> "Counterparty":
        if self.status != self.Status.PENDING:
            raise ValueError("Заявка уже обработана")

        # Если контрагент с таким ИНН уже есть — не создаём новый
        cp, created = Counterparty.objects.get_or_create(
            inn=self.inn,
            defaults=dict(
                kpp=self.kpp,
                ogrn=self.ogrn,
                name=self.name,
                full_name=self.full_name,
                registration_country=self.registration_country,
                address=self.address,
                actual_address=self.actual_address,
                bank_name=self.bank_name,
                bank_bik=self.bank_bik,
                bank_account=self.bank_account,
                website=self.website,
            ),
        )
        # Обязательно закрепляем менеджера-инициатора
        if self.manager:
            cp.managers.add(self.manager)

        # Мягкая синхронизация, если контрагент уже был
        if not created:
            fields_to_sync = [
                "kpp", "ogrn", "name", "full_name", "registration_country",
                "address", "actual_address", "bank_name", "bank_bik", "bank_account", "website",
            ]
            changed = False
            for f in fields_to_sync:
                new_val = getattr(self, f)
                if new_val and getattr(cp, f) != new_val:
                    setattr(cp, f, new_val)
                    changed = True
            if changed:
                cp.save()

        # Фиксируем решение и связь с контрагентом
        self.status = self.Status.APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.reviewer_comment = comment or ""
        self.counterparty = cp                  # <<< ВАЖНО
        self.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "reviewer_comment", "counterparty"
        ])

        return cp

    @transaction.atomic
    def reject(self, reviewer, comment: str | None = None):
        if self.status != self.Status.PENDING:
            raise ValueError("Заявка уже обработана")

        self.status = self.Status.REJECTED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.reviewer_comment = comment or ""
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "reviewer_comment"])

class CounterpartyCreateRequestDocument(models.Model):
    request = models.ForeignKey(
        "CounterpartyCreateRequest",
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Заявка",
    )
    title = models.CharField("Название", max_length=255, blank=True)
    file = models.FileField("Файл", upload_to="counterparty_request_docs/%Y/%m/%d/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Файл заявки на контрагента"
        verbose_name_plural = "Файлы заявки на контрагента"

    def __str__(self):
        return self.title or (self.file.name if self.file else "Файл")


@receiver(post_delete, sender=Counterparty)
def mark_requests_rejected_on_counterparty_delete(sender, instance: Counterparty, **kwargs):
    """
    Если удалили контрагента, все APPROVED-заявки, из которых он был создан,
    переводим в REJECTED, чтобы у менеджера это отразилось как 'Отклонено'.
    """
    try:
        CounterpartyCreateRequest.objects.filter(
            counterparty=instance,
            status=CounterpartyCreateRequest.Status.APPROVED
        ).update(
            status=CounterpartyCreateRequest.Status.REJECTED,
            reviewed_by=None,
            reviewed_at=timezone.now(),
            reviewer_comment="Автоматически: контрагент удалён",
        )
    except Exception:
        # Защита на случай миграций и т.п.
        pass



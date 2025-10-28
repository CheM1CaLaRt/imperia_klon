from django.core.management.base import BaseCommand
from core.models_requests import Request, RequestQuote

class Command(BaseCommand):
    help = "Удаляет все заявки и прикреплённые КП (файлы тоже)."

    def handle(self, *args, **options):
        # удалить файлы КП
        n_files = 0
        for q in RequestQuote.objects.all():
            if q.file:
                q.file.delete(save=False)
                n_files += 1
            q.delete()
        # удалить все заявки
        deleted = Request.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(
            f"Готово. КП файлов: {n_files}, удалено объектов: {deleted}"
        ))

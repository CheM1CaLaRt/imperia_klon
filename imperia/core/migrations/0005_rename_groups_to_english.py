from django.db import migrations

def rename_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')

    mapping = {
        'Склад': 'Warehouse',
        'Оператор': 'Operator',
        'Менеджер': 'Manager',
        'Управляющий': 'Director',
    }

    # Переименование, если найдены русские имена
    for ru, en in mapping.items():
        g = Group.objects.filter(name=ru).first()
        if g:
            # Если английская уже есть — просто удалим дубликат русскую
            if Group.objects.filter(name=en).exists():
                g.delete()
            else:
                g.name = en
                g.save()

    # Гарантируем наличие всех нужных групп
    required = ['Warehouse', 'Operator', 'Manager', 'Director']
    for name in required:
        Group.objects.get_or_create(name=name)

class Migration(migrations.Migration):
    dependencies = [
        ('core', '0003_create_default_groups'),  # или текущая последняя миграция
    ]
    operations = [
        migrations.RunPython(rename_groups, migrations.RunPython.noop),
    ]

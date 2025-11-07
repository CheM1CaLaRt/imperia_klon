
from django.db import migrations, models

def purge_orphan_pickitems(apps, schema_editor):
    PickItem = apps.get_model('core', 'PickItem')
    # Удаляем строки без связи с заявкой
    PickItem.objects.filter(request__isnull=True).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_alter_pickitem_request'),
    ]

    operations = [
        # 1) Чистим данные
        migrations.RunPython(purge_orphan_pickitems, migrations.RunPython.noop),

        # 2) Делаем поле обязательным
        migrations.AlterField(
            model_name='pickitem',
            name='request',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='pick_items',
                to='core.request',
                null=False,
                blank=False,
            ),
        ),
    ]
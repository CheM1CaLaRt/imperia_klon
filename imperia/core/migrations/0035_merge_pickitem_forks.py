from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_fill_pickitem_request_and_set_not_null'),
        ('core', '0034_add_missing_pickitem_timestamps'),
    ]

    # Нам ничего выполнять не нужно — просто «склеиваем» ветки графа.
    operations = []

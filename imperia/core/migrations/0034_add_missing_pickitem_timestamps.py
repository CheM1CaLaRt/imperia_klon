from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_remove_picklist_created_by_remove_picklist_request_and_more'),
    ]

    operations = [
        # created_at
        migrations.RunSQL(
            sql=(
                "ALTER TABLE core_pickitem "
                "ADD COLUMN IF NOT EXISTS created_at timestamp with time zone "
                "NOT NULL DEFAULT now();"
            ),
            reverse_sql=(
                # откат удалять не будем, чтобы не потерять данные; оставим пустым
                migrations.RunSQL.noop
            ),
        ),
        # updated_at
        migrations.RunSQL(
            sql=(
                "ALTER TABLE core_pickitem "
                "ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone "
                "NOT NULL DEFAULT now();"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        # (опционально) проставим updated_at = now() там, где NULL в старых БД
        migrations.RunSQL(
            sql=(
                "UPDATE core_pickitem "
                "SET updated_at = now() "
                "WHERE updated_at IS NULL;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]


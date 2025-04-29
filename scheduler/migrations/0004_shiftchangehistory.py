# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0003_auto_20250421_1404'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftChangeHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('previous_shift', models.CharField(choices=[('D', '데이'), ('E', '이브닝'), ('N', '나이트'), ('OFF', '휴무')], max_length=3)),
                ('new_shift', models.CharField(choices=[('D', '데이'), ('E', '이브닝'), ('N', '나이트'), ('OFF', '휴무')], max_length=3)),
                ('change_time', models.DateTimeField(auto_now_add=True)),
                ('change_number', models.PositiveIntegerField(default=1, help_text='해당 날짜/간호사에 대한 변경 순서 번호')),
                ('nurse', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='scheduler.nurse')),
            ],
            options={
                'ordering': ['-change_time'],
            },
        ),
    ] 
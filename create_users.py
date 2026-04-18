import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')
django.setup()

from accounts.models import User

if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
    print("Created Superuser: admin / admin123")

roles = [
    ('accountant', 'ACCOUNTANT'),
    ('supplier', 'SUPPLIER'),
    ('owner', 'OWNER')
]

for username, role in roles:
    if not User.objects.filter(username=username).exists():
        user = User.objects.create_user(username, f'{username}@example.com', f'{username}123')
        user.role = role
        user.save()
        print(f"Created User: {username} / {username}123 with role {role}")

print("Database seeded with test users!")

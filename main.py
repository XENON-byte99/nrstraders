import os
import sys
import django

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'nrs_project.settings')

def main():
    print("Starting Android Django bootstrap...")
    
    # Initialize Django
    django.setup()
    
    from django.core.management import execute_from_command_line

    # 1. Run migrations to set up the SQLite database on the mobile device
    print("Running database migrations...")
    try:
        execute_from_command_line(['manage.py', 'migrate', '--noinput'])
        print("Migrations complete.")
    except Exception as e:
        print(f"Error during migration: {e}")

    # 2. Check and create default superuser
    print("Checking superuser status...")
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if not User.objects.filter(is_superuser=True).exists():
            print("No superuser found. Creating default admin superuser...")
            User.objects.create_superuser('admin', 'admin@example.com', 'admin1234')
            print("Default superuser created: username 'admin', password 'admin1234'")
        else:
            print("Superuser already exists.")
    except Exception as e:
        print(f"Error checking/creating superuser: {e}")

    # 3. Start the Django development server
    # We run on 127.0.0.1:8000 (localhost)
    # --noreload is critical to prevent subprocess spawning issues on Android
    print("Starting Django server on http://127.0.0.1:8000 ...")
    try:
        execute_from_command_line(['manage.py', 'runserver', '127.0.0.1:8000', '--noreload'])
    except Exception as e:
        print(f"Django server exited with error: {e}")

if __name__ == '__main__':
    main()

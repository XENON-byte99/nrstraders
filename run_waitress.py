import os
import sys
from waitress import serve
from django.core.wsgi import get_wsgi_application

# Ensure the current directory is in python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set Django settings module
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nrs_project.settings")

# Load WSGI application
application = get_wsgi_application()

if __name__ == "__main__":
    # Waitress is a high-performance, multi-threaded production WSGI server for Windows.
    print("=========================================================================")
    print("  Starting production WSGI server (Waitress) for NRS SOFTWARE            ")
    print("  Address: http://localhost:8002 or http://127.0.0.1:8002               ")
    print("  To stop the server, press Ctrl + C                                     ")
    print("=========================================================================")
    serve(application, host="0.0.0.0", port=8002, threads=6)

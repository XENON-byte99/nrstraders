"""Start the billing site inside the mobile app.

The Android app calls ``start(data_dir)`` once; it prepares the on-device
database and serves the site on 127.0.0.1 (never reachable from the network).
Can also be run on a computer to try the offline build:

    python mobile/server.py            # then open the printed address
"""
import os
import socket
import sys
import threading
from pathlib import Path

_state = {'port': None, 'error': None}
_lock = threading.Lock()


def _free_port(preferred):
    for port in (preferred, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Without this, restarting the app right after it closed finds the
        # usual port "busy" for a minute and has to move to another one.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError('no free port')


def start(data_dir, app_dir=None, preferred_port=8765):
    """Prepare the database and serve the site. Returns the port."""
    with _lock:
        if _state['port']:
            return _state['port']
        if app_dir and app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        os.environ['NRS_DATA_DIR'] = str(data_dir)
        os.environ['DJANGO_SETTINGS_MODULE'] = 'nrs_project.settings_mobile'

        import django
        django.setup()
        from django.core.management import call_command
        call_command('migrate', interactive=False, verbosity=0)

        from django.core.wsgi import get_wsgi_application
        from waitress.server import create_server
        port = _free_port(preferred_port)
        server = create_server(get_wsgi_application(), host='127.0.0.1', port=port, threads=6,
                               max_request_body_size=64 * 1024 * 1024)
        threading.Thread(target=server.run, name='nrs-server', daemon=True).start()
        from documents import sync_client
        sync_client.start_auto_sync()
        _state['port'] = port
        return port


if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    port = start(os.environ.get('NRS_DATA_DIR') or str(root / 'mobile_data'))
    print('NRS offline build running at http://127.0.0.1:%d/' % port)
    threading.Event().wait()

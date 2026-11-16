"""Entry point the Android app calls to start the billing site."""
import sys
import threading


def start(data_dir, app_dir):
    """Serve the site on 127.0.0.1 and return the port."""
    # Android gives native threads small stacks; Django's template engine and
    # migration planner recurse deeply enough to need more.
    threading.stack_size(4 * 1024 * 1024)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    from mobile import server
    return server.start(data_dir, app_dir)

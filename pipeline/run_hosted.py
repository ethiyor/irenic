"""Render entry point: one web process and one bounded scheduler on the same disk."""
import logging
import os
from pathlib import Path
import threading
import signal

from waitress import create_server
from outreach_console import initialize_demo
from outreach_hosted import create_app, environment


def main():
    os.umask(0o077)
    cfg = environment()
    if cfg['demo'] and not (Path(cfg['run'])/'live.sqlite3').exists():
        initialize_demo(cfg['run'])
    app = create_app(cfg)
    service = app.extensions['outreach_service']
    stopping = threading.Event()
    def scheduler():
        while not stopping.is_set():
            try:
                service.run_due()
            except Exception:
                logging.error('Scheduler unavailable; inspect service state. No provider details logged.')
            stopping.wait(15)
    worker = threading.Thread(target=scheduler, name='outreach-scheduler', daemon=True)
    worker.start()
    # Waitress is one process; do not scale this SQLite deployment horizontally.
    server = create_server(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8000')), threads=4,
                           max_request_body_size=8192, channel_timeout=60)
    def stop(*_):
        stopping.set()
        worker.join(timeout=25)
        server.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.run()


if __name__ == '__main__':
    main()

"""Render entry point: one web process and one bounded scheduler on the same disk."""
import logging
import os
from pathlib import Path
import threading
import signal

from waitress import create_server
from outreach_console import initialize_demo
from outreach_hosted import create_app, environment


def maintenance(environ, start_response):
    """Keep operator SSH reachable after a lock blocks startup; expose no records."""
    health = environ.get('PATH_INFO') == '/healthz'
    body = b'{"status":"maintenance","ready":false}' if health else b'Workspace temporarily unavailable. Operator recovery is in progress.'
    start_response('200 OK' if health else '503 Service Unavailable',
        [('Content-Type','application/json' if health else 'text/plain'),('Cache-Control','no-store'),
         ('X-Content-Type-Options','nosniff'),('Content-Length',str(len(body)))])
    return [body]


def main():
    os.umask(0o077)
    cfg = environment()
    if cfg['demo'] and not (Path(cfg['run'])/'live.sqlite3').exists():
        initialize_demo(cfg['run'])
    try:
        app = create_app(cfg)
    except FileExistsError:
        logging.error('Startup blocked by existing lock. Maintenance only; no scheduler or mail actions. Operator must verify and recover.')
        create_server(maintenance, host='0.0.0.0', port=int(os.environ.get('PORT','8000')), threads=2).run()
        return
    service = app.extensions['outreach_workspaces']
    stopping = threading.Event()
    def scheduler():
        while not stopping.is_set():
            try:
                service.run_due()
                from outreach_backup import automatic
                automatic(service)
            except Exception:
                logging.error('Scheduler unavailable; inspect service state. No provider details logged.')
            stopping.wait(15)
    worker = threading.Thread(target=scheduler, name='outreach-scheduler', daemon=True)
    worker.start()
    # Public sources have a separate bounded worker; Gmail/approval paths never call it.
    def source_scheduler():
        while not stopping.is_set():
            try:
                app.extensions['source_monitor'].tick()
            except Exception:
                logging.error('Source monitor unavailable; inspect its status. No source content logged.')
            stopping.wait(15)
    source_worker = threading.Thread(target=source_scheduler, name='source-monitor', daemon=True)
    source_worker.start()
    # Waitress is one process; do not scale this SQLite deployment horizontally.
    server = create_server(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8000')), threads=4,
                           max_request_body_size=65536, channel_timeout=60)
    def stop(*_):
        stopping.set()
        worker.join(timeout=25)
        source_worker.join(timeout=1)
        server.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.run()


if __name__ == '__main__':
    main()

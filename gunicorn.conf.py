"""Gunicorn configuration for production."""

# Server socket
bind = "0.0.0.0:5050"
backlog = 2048

# Worker processes — capped at 2 to stay within RAM budget alongside Kokoro/PyTorch
workers = 2
worker_class = "sync"
worker_connections = 1000
timeout = 300
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "article_scraper"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

"""Gunicorn configuration for production (Flask-only mode)."""

# Server socket
bind = "0.0.0.0:5050"
backlog = 2048

# Workers — threads for background video generation; capped to fit RAM alongside Kokoro/PyTorch
workers = 2
threads = 4
worker_class = "gthread"
timeout = 300  # Video gen can take a while
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Process
proc_name = "clipper"
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

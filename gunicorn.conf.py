"""Gunicorn configuration for production (Flask-only mode)."""

# Server socket
bind = "0.0.0.0:5050"
backlog = 2048

# Workers — use threads for background video generation
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

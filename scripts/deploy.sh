#!/bin/bash
# Deploy Clipper to Oracle Cloud Free Tier
# Usage: ./scripts/deploy.sh <SSH_KEY_PATH> <SERVER_IP>

set -e

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: ./scripts/deploy.sh <SSH_KEY_PATH> <SERVER_IP>"
    echo "Example: ./scripts/deploy.sh ~/.ssh/oracle-key.pem 129.154.xxx.xxx"
    exit 1
fi

SSH_KEY="$1"
SERVER_IP="$2"
SSH_CMD="ssh -i $SSH_KEY ubuntu@$SERVER_IP"
SCP_CMD="scp -i $SSH_KEY"

echo "=========================================="
echo "  Deploying Clipper to: $SERVER_IP"
echo "=========================================="

# Step 1: Create deployment archive (no Node.js, no tests)
echo "[1/4] Creating archive..."
tar -czf /tmp/clipper.tar.gz \
    --exclude='.venv' \
    --exclude='instance' \
    --exclude='static/videos/*' \
    --exclude='static/carousels/*' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='.DS_Store' \
    .

# Step 2: Upload
echo "[2/4] Uploading..."
$SCP_CMD /tmp/clipper.tar.gz ubuntu@$SERVER_IP:~

if [ -f .env ]; then
    echo "       Uploading .env..."
    $SCP_CMD .env ubuntu@$SERVER_IP:~
fi

# Step 3: Deploy on server
echo "[3/4] Deploying on server..."
$SSH_CMD << 'ENDSSH'
set -e

APP_DIR=/var/www/clipper

# Install deps if first run
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker ubuntu
    sudo systemctl enable docker
fi

# Setup app directory
sudo mkdir -p $APP_DIR
sudo chown -R ubuntu:ubuntu $APP_DIR
cd $APP_DIR

# Extract new code
tar -xzf ~/clipper.tar.gz

# Move .env
if [ -f ~/.env ]; then
    mv ~/.env $APP_DIR/.env
fi

# Create data directories
mkdir -p instance static/videos static/carousels

# Build and run with Docker Compose
docker compose down 2>/dev/null || true
docker compose build --no-cache
docker compose up -d

echo "Deployment complete!"
ENDSSH

# Step 4: Verify
echo "[4/4] Verifying..."
sleep 10
$SSH_CMD "cd /var/www/clipper && docker compose ps && docker compose logs --tail=20"

echo ""
echo "=========================================="
echo "  Deployment Complete!"
echo "=========================================="
echo ""
echo "  Dashboard: http://$SERVER_IP:5050"
echo "  Health:    http://$SERVER_IP:5050/api/health"
echo ""
echo "  Logs: $SSH_CMD 'cd /var/www/clipper && docker compose logs -f'"
echo ""

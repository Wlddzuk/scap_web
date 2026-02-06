#!/bin/bash
# Local deployment script - run from your machine
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
echo "  Deploying to Oracle Cloud: $SERVER_IP"
echo "=========================================="

# Step 1: Create deployment archive
echo "[1/5] Creating deployment archive..."
tar -czf /tmp/app.tar.gz \
    --exclude='.venv' \
    --exclude='instance' \
    --exclude='static/videos/*' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='.env' \
    .

# Step 2: Copy files to server
echo "[2/5] Uploading files to server..."
$SCP_CMD /tmp/app.tar.gz ubuntu@$SERVER_IP:~
$SCP_CMD scripts/setup_oracle.sh ubuntu@$SERVER_IP:~
$SCP_CMD scripts/tiktok-video-gen.service ubuntu@$SERVER_IP:~
$SCP_CMD scripts/nginx.conf ubuntu@$SERVER_IP:~

# Check if .env exists locally
if [ -f .env ]; then
    echo "       Uploading .env file..."
    $SCP_CMD .env ubuntu@$SERVER_IP:~
else
    echo "       WARNING: No .env file found. You'll need to create one on the server."
fi

# Step 3: Run setup on server
echo "[3/5] Running setup on server..."
$SSH_CMD "chmod +x ~/setup_oracle.sh && sudo ~/setup_oracle.sh"

# Step 4: Deploy application
echo "[4/5] Deploying application..."
$SSH_CMD << 'ENDSSH'
set -e

# Extract application
sudo rm -rf /var/www/tiktok-video-gen/*
sudo tar -xzf ~/app.tar.gz -C /var/www/tiktok-video-gen
sudo chown -R ubuntu:ubuntu /var/www/tiktok-video-gen

# Move .env if it exists
if [ -f ~/.env ]; then
    sudo mv ~/.env /var/www/tiktok-video-gen/
    sudo chown ubuntu:ubuntu /var/www/tiktok-video-gen/.env
fi

# Create required directories
mkdir -p /var/www/tiktok-video-gen/instance
mkdir -p /var/www/tiktok-video-gen/static/videos

# Set up Python environment
cd /var/www/tiktok-video-gen
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install wheel
pip install -r requirements.txt

# Install systemd service
sudo mv ~/tiktok-video-gen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tiktok-video-gen
sudo systemctl restart tiktok-video-gen

# Install Nginx config
sudo mv ~/nginx.conf /etc/nginx/sites-available/tiktok-video-gen
sudo ln -sf /etc/nginx/sites-available/tiktok-video-gen /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

echo "Deployment complete!"
ENDSSH

# Step 5: Verify deployment
echo "[5/5] Verifying deployment..."
sleep 5
$SSH_CMD "sudo systemctl status tiktok-video-gen --no-pager"

echo ""
echo "=========================================="
echo "  Deployment Complete!"
echo "=========================================="
echo ""
echo "Your app is now available at:"
echo "  http://$SERVER_IP"
echo ""
echo "Useful commands:"
echo "  View logs:    $SSH_CMD 'sudo journalctl -u tiktok-video-gen -f'"
echo "  Restart app:  $SSH_CMD 'sudo systemctl restart tiktok-video-gen'"
echo ""

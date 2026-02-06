#!/bin/bash
# Oracle Cloud Ubuntu Setup Script for TikTok Video Generator
# Run as: sudo ./setup_oracle.sh

set -e

echo "=========================================="
echo "  Oracle Cloud Setup for Video Generator"
echo "=========================================="

# Update system
echo "[1/7] Updating system packages..."
apt update && apt upgrade -y

# Install Python 3.11
echo "[2/7] Installing Python 3.11..."
apt install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt update
apt install -y python3.11 python3.11-venv python3.11-dev python3-pip

# Install FFmpeg
echo "[3/7] Installing FFmpeg..."
apt install -y ffmpeg

# Install other dependencies
echo "[4/7] Installing system dependencies..."
apt install -y nginx git curl wget build-essential libffi-dev libssl-dev

# Configure iptables (Oracle Cloud requires this)
echo "[5/7] Configuring firewall..."
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 5050 -j ACCEPT
netfilter-persistent save

# Create application directory
echo "[6/7] Creating application directory..."
mkdir -p /var/www/tiktok-video-gen
mkdir -p /var/www/tiktok-video-gen/instance
mkdir -p /var/www/tiktok-video-gen/static/videos
chown -R ubuntu:ubuntu /var/www/tiktok-video-gen

# Install netfilter-persistent to save iptables rules
echo "[7/7] Making firewall rules persistent..."
apt install -y iptables-persistent

echo ""
echo "=========================================="
echo "  Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Upload your application code"
echo "2. Configure .env file"
echo "3. Set up systemd service"
echo "4. Configure Nginx"
echo ""
echo "Python version: $(python3.11 --version)"
echo "FFmpeg version: $(ffmpeg -version | head -1)"
echo ""

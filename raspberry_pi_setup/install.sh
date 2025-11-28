#!/bin/bash
# Café Tiko POS - Raspberry Pi Installation Script
# Run this on your Raspberry Pi

set -e

echo "=========================================="
echo "  Café Tiko POS - Raspberry Pi Setup"
echo "=========================================="

# Update system
echo "[1/7] Updating system..."
sudo apt update && sudo apt upgrade -y

# Install dependencies
echo "[2/7] Installing dependencies..."
sudo apt install -y python3 python3-pip python3-venv git sqlite3

# Create app directory
echo "[3/7] Setting up application directory..."
APP_DIR="/home/$USER/pos"
mkdir -p $APP_DIR

# Check if we're running from the repo or need to clone
if [ -f "./app.py" ]; then
    echo "Copying files from current directory..."
    cp -r ./* $APP_DIR/
else
    echo "Please copy your POS files to $APP_DIR"
    echo "Or clone your repository:"
    echo "  git clone <your-repo-url> $APP_DIR"
fi

cd $APP_DIR

# Create virtual environment
echo "[4/7] Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "[5/7] Installing Python dependencies..."
pip install --upgrade pip
pip install flask requests gunicorn

# Create environment file
echo "[6/7] Creating environment configuration..."
cat > $APP_DIR/.env << 'EOF'
FLASK_SECRET_KEY=change-this-to-a-random-string
BILLINGO_ENV=prod
EOF

echo ""
echo "IMPORTANT: Edit $APP_DIR/.env and set a secure FLASK_SECRET_KEY"
echo ""

# Install systemd service
echo "[7/7] Installing systemd service..."
sudo cp raspberry_pi_setup/pos.service /etc/systemd/system/pos.service
sudo sed -i "s|/home/pi|/home/$USER|g" /etc/systemd/system/pos.service
sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/pos.service
sudo systemctl daemon-reload
sudo systemctl enable pos.service

echo ""
echo "=========================================="
echo "  Installation Complete!"
echo "=========================================="
echo ""
echo "Commands:"
echo "  Start:   sudo systemctl start pos"
echo "  Stop:    sudo systemctl stop pos"
echo "  Status:  sudo systemctl status pos"
echo "  Logs:    journalctl -u pos -f"
echo ""
echo "Access your POS at: http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "Next steps:"
echo "  1. Edit $APP_DIR/.env with your settings"
echo "  2. Copy your database (pos_prod.db) to $APP_DIR"
echo "  3. Run: sudo systemctl start pos"
echo ""

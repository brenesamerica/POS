#!/bin/bash
# Install both Production and Test services

set -e

echo "Installing POS services (Production + Test)..."

# Copy service files
sudo cp pos-prod.service /etc/systemd/system/
sudo cp pos-test.service /etc/systemd/system/

# Update paths for current user
sudo sed -i "s|/home/pi|/home/$USER|g" /etc/systemd/system/pos-prod.service
sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/pos-prod.service
sudo sed -i "s|/home/pi|/home/$USER|g" /etc/systemd/system/pos-test.service
sudo sed -i "s|User=pi|User=$USER|g" /etc/systemd/system/pos-test.service

# Reload systemd
sudo systemctl daemon-reload

# Enable both services (start on boot)
sudo systemctl enable pos-prod
sudo systemctl enable pos-test

echo ""
echo "=========================================="
echo "  Both services installed!"
echo "=========================================="
echo ""
echo "Production (port 5000):"
echo "  Start:  sudo systemctl start pos-prod"
echo "  Stop:   sudo systemctl stop pos-prod"
echo "  Logs:   journalctl -u pos-prod -f"
echo "  URL:    http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo "Test (port 5001):"
echo "  Start:  sudo systemctl start pos-test"
echo "  Stop:   sudo systemctl stop pos-test"
echo "  Logs:   journalctl -u pos-test -f"
echo "  URL:    http://$(hostname -I | awk '{print $1}'):5001"
echo ""
echo "Start both now:"
echo "  sudo systemctl start pos-prod pos-test"
echo ""

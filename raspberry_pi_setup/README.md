# Café Tiko POS - Raspberry Pi Setup

## Requirements
- Raspberry Pi 3/4/5 (any model with 1GB+ RAM)
- MicroSD card 16GB+
- Raspberry Pi OS (Lite or Desktop)
- Network connection (WiFi or Ethernet)

## Quick Install

### 1. Prepare your Raspberry Pi
Install Raspberry Pi OS using Raspberry Pi Imager, then SSH into it or open terminal.

### 2. Copy files to Raspberry Pi
From your Windows PC, copy the entire POS folder to the Pi:
```bash
scp -r /path/to/POS pi@raspberrypi.local:~/pos
```

Or on the Pi, clone from git:
```bash
git clone <your-repo-url> ~/pos
```

### 3. Run the installer
```bash
cd ~/pos/raspberry_pi_setup
chmod +x install.sh
./install.sh
```

### 4. Configure
Edit the environment file:
```bash
nano ~/pos/.env
```

Set your values:
```
FLASK_SECRET_KEY=your-secure-random-key
BILLINGO_ENV=prod
```

### 5. Copy your database
Copy your production database from Windows:
```bash
scp /path/to/pos_prod.db pi@raspberrypi.local:~/pos/
```

### 6. Start the service
```bash
sudo systemctl start pos
```

## Usage

| Command | Description |
|---------|-------------|
| `sudo systemctl start pos` | Start POS |
| `sudo systemctl stop pos` | Stop POS |
| `sudo systemctl restart pos` | Restart POS |
| `sudo systemctl status pos` | Check status |
| `journalctl -u pos -f` | View live logs |

## Access
- Local network: `http://raspberrypi.local:5000` or `http://<pi-ip>:5000`
- Find IP: `hostname -I`

## Remote Access with Tailscale
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```
Then access via Tailscale IP from anywhere.

## Automatic Backups
Set up daily backups at 2 AM:
```bash
chmod +x ~/pos/raspberry_pi_setup/backup.sh
crontab -e
```
Add this line:
```
0 2 * * * /home/pi/pos/raspberry_pi_setup/backup.sh
```

## Troubleshooting

### Service won't start
```bash
journalctl -u pos -n 50 --no-pager
```

### Database locked errors
Only one instance should run. Check:
```bash
ps aux | grep python
```

### Permission errors
```bash
sudo chown -R pi:pi ~/pos
```

### Update the application
```bash
cd ~/pos
git pull
sudo systemctl restart pos
```

## Optional: UPS for Power Protection
Consider a Pi UPS HAT (~$25) to survive power outages and prevent database corruption.

Recommended: Geekworm X728 or PiJuice

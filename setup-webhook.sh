mkdir -p /root/.github-webhook

# Installer le webhook listener
apt install -y python3-pip
pip3 install flask gunicorn

# Creer le serveur webhook
cat > /root/.github-webhook/webhook.py << 'PYEOF'
from flask import Flask, request
import subprocess
import hmac
import hashlib
import os

app = Flask(__name__)
SECRET = "copromanager-webhook-secret-2025"

@app.route("/webhook", methods=["POST"])
def webhook():
    # Verifier la signature GitHub
    signature = request.headers.get("X-Hub-Signature-256", "")
    payload = request.get_data()
    expected = "sha256=" + hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return "Unauthorized", 401
    
    # Lancer le deploiement
    subprocess.Popen(["/bin/bash", "/root/deploy.sh"])
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)
PYEOF

# Creer le service systemd
cat > /etc/systemd/system/github-webhook.service << 'SVCEOF'
[Unit]
Description=GitHub Webhook for CoproManager
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /root/.github-webhook/webhook.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

# Activer et demarrer
systemctl daemon-reload
systemctl enable github-webhook
systemctl start github-webhook

# Ouvrir le port
ufw allow 9000/tcp

echo ""
echo "========================================"
echo "  Webhook installe sur le port 9000"
echo "========================================"
echo ""
echo "  Configurez GitHub Webhook:"
echo "  URL: http://51.158.112.243:9000/webhook"
echo "  Secret: copromanager-webhook-secret-2025"
echo "  Events: Just the push event"
echo "========================================"

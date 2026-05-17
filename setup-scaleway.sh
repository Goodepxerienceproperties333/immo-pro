#!/bin/bash
# ============================================
# CoproManager - Installation automatique
# Scaleway Paris - Conforme RGPD
# ============================================

set -e

echo "========================================"
echo "  CoproManager - Installation Scaleway"
echo "========================================"

# 1. Mise a jour systeme
echo "[1/6] Mise a jour du systeme..."
apt update -y && apt upgrade -y

# 2. Installer Docker
echo "[2/6] Installation de Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Installer Docker Compose plugin
apt install -y docker-compose-plugin git

echo "Docker $(docker --version)"
echo "Docker Compose $(docker compose version)"

# 3. Cloner le repository
echo "[3/6] Clonage du repository..."
cd /root
if [ -d "immo-pro" ]; then
    cd immo-pro && git pull
else
    git clone https://github.com/Goodepxerienceproperties333/immo-pro.git
    cd immo-pro
fi

# 4. Configurer l'environnement
echo "[4/6] Configuration..."
SERVER_IP=$(curl -s ifconfig.me)

cat > .env << ENVEOF
FRONTEND_URL=http://${SERVER_IP}
JWT_SECRET=$(openssl rand -hex 32)
ADMIN_EMAIL=admin@copro.be
ADMIN_PASSWORD=CoproAdmin2025!
ENVEOF

echo "Configuration creee avec IP: ${SERVER_IP}"

# 5. Construire et lancer
echo "[5/6] Construction des containers (patience, 2-3 minutes)..."
docker compose up -d --build

# 6. Verifier
echo "[6/6] Verification..."
sleep 10
docker compose ps

echo ""
echo "========================================"
echo "  INSTALLATION TERMINEE !"
echo "========================================"
echo ""
echo "  Votre application est accessible sur:"
echo "  http://${SERVER_IP}"
echo ""
echo "  Connexion admin:"
echo "  Email: admin@copro.be"
echo "  Mot de passe: CoproAdmin2025!"
echo ""
echo "  IMPORTANT: Changez le mot de passe admin !"
echo ""
echo "  Commandes utiles:"
echo "  - Logs:      docker compose logs -f"
echo "  - Redemarrer: docker compose restart"
echo "  - Arreter:   docker compose down"
echo "========================================"

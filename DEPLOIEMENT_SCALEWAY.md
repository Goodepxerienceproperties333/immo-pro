# ============================================
# DEPLOIEMENT COPROMANAGER SUR SCALEWAY
# Guide pas a pas
# ============================================

## 1. CREER UNE INSTANCE SCALEWAY

1. Connectez-vous sur https://console.scaleway.com
2. Allez dans **Instances** > **Creer une instance**
3. Choisissez:
   - **Region**: Paris (PAR1) - conformite RGPD
   - **Type**: DEV1-M (3 vCPU, 4GB RAM) ou GP1-XS pour production
   - **Image**: Ubuntu 22.04 LTS
   - **Stockage**: 40GB SSD minimum
4. Ajoutez votre cle SSH
5. Creez l'instance

## 2. CONFIGURER LE SERVEUR

Connectez-vous en SSH:
```bash
ssh root@VOTRE_IP_SCALEWAY
```

Installez Docker et Docker Compose:
```bash
# Mise a jour
apt update && apt upgrade -y

# Installer Docker
curl -fsSL https://get.docker.com | sh

# Installer Docker Compose
apt install -y docker-compose-plugin

# Verifier
docker --version
docker compose version
```

## 3. DEPLOYER L'APPLICATION

```bash
# Cloner le repo
git clone https://github.com/Goodepxerienceproperties333/immo-pro.git
cd immo-pro

# Configurer l'environnement
cp .env.production .env

# IMPORTANT: Editez le fichier .env
nano .env
```

Dans le fichier .env, modifiez:
```
FRONTEND_URL=https://copro.votredomaine.be   (ou http://VOTRE_IP_SCALEWAY si pas de domaine)
JWT_SECRET=une_vraie_cle_secrete_aleatoire_de_64_caracteres
ADMIN_EMAIL=votre@email.be
ADMIN_PASSWORD=un_mot_de_passe_fort
```

Lancez l'application:
```bash
# Construire et demarrer
docker compose up -d --build

# Verifier que tout tourne
docker compose ps

# Voir les logs
docker compose logs -f
```

## 4. CONFIGURER LE NOM DE DOMAINE (optionnel mais recommande)

1. Dans votre registrar DNS, creez un enregistrement A:
   - `copro.votredomaine.be` -> VOTRE_IP_SCALEWAY

2. Installez Certbot pour HTTPS gratuit (Let's Encrypt):
```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d copro.votredomaine.be
```

## 5. SECURISER LE SERVEUR

```bash
# Firewall
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw enable

# Desactiver l'acces MongoDB depuis l'exterieur (IMPORTANT)
# MongoDB n'est accessible que depuis Docker, pas depuis Internet
```

## 6. SAUVEGARDES AUTOMATIQUES

Creez un script de backup MongoDB:
```bash
cat > /root/backup-mongo.sh << 'EOF'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M)
docker compose exec -T mongodb mongodump --db copromanager --archive > /root/backups/mongo_$DATE.gz
# Garder les 30 derniers backups
ls -t /root/backups/mongo_*.gz | tail -n +31 | xargs rm -f 2>/dev/null
EOF

chmod +x /root/backup-mongo.sh
mkdir -p /root/backups

# Backup automatique chaque nuit a 2h du matin
echo "0 2 * * * /root/backup-mongo.sh" | crontab -
```

## 7. COMMANDES UTILES

```bash
# Redemarrer l'application
docker compose restart

# Voir les logs backend
docker compose logs -f backend

# Voir les logs frontend
docker compose logs -f frontend

# Arreter l'application
docker compose down

# Mettre a jour (apres un git pull)
git pull
docker compose up -d --build

# Acces au shell MongoDB
docker compose exec mongodb mongosh copromanager
```

## 8. ESTIMATION DES COUTS SCALEWAY

| Service          | Type     | Prix/mois  |
|------------------|----------|------------|
| Instance DEV1-M  | Compute  | ~7 EUR     |
| Stockage 40GB    | SSD      | ~4 EUR     |
| IP publique      | Reseau   | ~3 EUR     |
| **TOTAL**        |          | **~14 EUR/mois** |

Pour la production avec plus de performances:
| Service          | Type     | Prix/mois  |
|------------------|----------|------------|
| Instance GP1-XS  | Compute  | ~20 EUR    |
| Stockage 80GB    | SSD      | ~8 EUR     |
| Managed MongoDB  | Database | ~15 EUR    |
| **TOTAL**        |          | **~43 EUR/mois** |

## CONFORMITE RGPD

- Donnees stockees a **Paris (PAR1)** - serveurs en France/UE
- Scaleway est une entreprise **francaise** (Groupe Iliad)
- Conforme au RGPD europeenne
- Donnees souveraines - pas de transfert hors UE

#!/bin/bash
# ============================================
# CoproManager - Auto-deploy webhook
# Se met a jour automatiquement via GitHub
# ============================================

REPO_DIR="/root/immo-pro"
LOG_FILE="/var/log/copromanager-deploy.log"

echo "$(date) - Deploiement declenche" >> $LOG_FILE

cd $REPO_DIR

# Pull les derniers changements
git pull origin main >> $LOG_FILE 2>&1

# Rebuild et redemarrer
docker compose up -d --build >> $LOG_FILE 2>&1

echo "$(date) - Deploiement termine" >> $LOG_FILE

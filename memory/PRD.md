# NextGe Copro - PRD (Product Requirements Document)

## Problem Statement
Application de gestion de copropriete basee sur le droit belge (PCMN), incluant la gestion stricte des roles, le cloisonnement des donnees (Chinese Wall), la gestion des imports CODA/Optipro, et les verrous fiscaux.

## Core Requirements
- Generation d'un PDF "Decompte de mutation" clair
- Application stricte des regles comptables belges
- Persistance fiable
- Portail proprietaire
- Imports IA
- Systeme legalement blinde

## Architecture
- Backend: FastAPI + MongoDB (Motor async)
- Frontend: React + Tailwind CSS + Shadcn UI
- Auth: JWT-based
- 3rd Party: Emergent LLM Key (Claude Sonnet text gen), Microsoft Graph (Email)

## Key DB Schema
- `coproprietes`: includes `promoter_owner_id`
- `properties`: `ownership_history` array (supports `promoteur_initial` type)
- `owners`: unique index on `auxiliary_code` + `copropriete_id`

## Completed Features
- Full accounting system (PCMN plan comptable, journals, grand livre)
- Import Wizard (Optipro/Sogis migration: owners, lots, suppliers, natures, budgets, distribution keys, invoices, journals, opening balance, OD entries)
- ACP Creation Assistant with Promoter Mode
- Owner management with syndic-wide search and duplicate merge
- Fiscal years management
- Invoicing (suppliers, owners)
- Banking (CODA import, lettrage)
- Reports (Bilan, Resultats, Balance de Tiers, Decompte mutation PDF)
- Distribution keys
- Communication (emails, templates)
- Admin platform (users, roles, audit log, RGPD, backups)
- Owner portal
- Fund calls
- Meters
- Reminders
- Document management
- Support chat bubble
- Legal docs (CGU, Privacy, Mentions, Cookies, Disclaimer)
- Cookie banner
- Onboarding dialog
- Release notes
- TopNav horizontal navigation (desktop)
- Mobile responsive layout with sidebar drawer

## Recent Changes (2026-07-22)
- FIXED: Layout responsive dynamique — supprime max-w-[1600px]/max-w-[1400px] contrainte, contenu 100% largeur + scrollbar-gutter stable
- Restructured FiscalYearPage.js: Tabs + action buttons on same line (flex justify-between), removed 70px gap
- Restructured DocumentsPage.js: Same pattern applied, buttons contextual per active tab
- Added "Terminer et aller a la Comptabilite" button on Wizard final recap screen
- Added "Terminer et aller a la Comptabilite" button on LotsPage mutation banner
- Existing "Ouvrir l'ACP" buttons demoted to secondary/outline style

## Pending Issues
- P0: CSS/UI layout shift on ACP screen (mobile - content shifted right with whitespace on left)
- P1: TEUWEN owner mapping & distribution lines logic in PDF/Reports (reports.py ~line 1558, pdf_decompte.py ~line 420)

## Backlog (Prioritized)
- P1: UI Frontend Modal for Statement Deletion (warning before deleting bank statement showing cancelled lettrages count)
- P2: Export Journals to CSV and PDF based on date selector
- P3: Admin tool to bulk reset legacy paid invoices back to unpaid status
- P4: Certificat fiscal annuel
- P5: Automated debt collection emails (APScheduler daily job)

## Refactoring Needs
- `import_wizard.py` (>3300 lines) needs to be split for maintainability

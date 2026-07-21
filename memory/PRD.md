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

## Tech Stack
- Backend: FastAPI, Async MongoDB (Motor), Pytest
- Frontend: React
- Database: MongoDB
- Auth: JWT cookie-based
- Accounting: PCMN strict, Master/Slave data integrity

## Architecture
- Bank Statements = Master -> Journal Entries (FI) = Slave -> Lettrage = Slave
- Cascading deletes: Delete statement -> Delete FIs -> Cancel lettrages -> Restore unpaid invoices

## Completed Features
- Import Wizard with 6->8 digit bank account remapping (iter90jk)
- Bilan: dedup FI, single-side owner display, merge owners script (iter90jl)
- Master/Slave Bank->FI sync with statement_line_id (iter90jm)
- Master/Slave Lettrage cascade on statement delete (iter90jn)
- **Compte 499 fix**: Regle belge stricte 499 = Provisions (cl.70) - Charges (cl.6). Produits hors provisions affiches separement (2026-07-21)

## Pending Issues
- P1: TEUWEN Owner mapping & distribution lines logic (NOT STARTED)
- P1: Bug 499 Balance Sheet 2EUR lines ignored (superceded by compte 499 formula fix)
- P2: 10EUR difference Optipro import vs Liste des depenses (BLOCKED - user PDF needed)

## Upcoming Tasks
- P1: UI Modal for Statement Deletion warning
- P2: UI "Merge Owners" admin page
- P3: Export Journals CSV/PDF with date selector
- P4: Admin bulk reset paid invoices to unpaid
- P5: Certificat fiscal annuel
- P6: Automated debt collection emails (APScheduler)

## Refactoring Needed
- reports.py (>3800 lines) - split into modules
- banking.py (>3000 lines) - split into modules

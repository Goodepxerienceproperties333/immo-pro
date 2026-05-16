# CoproManager - PRD (Product Requirements Document)

## Problem Statement
Application de gestion de copropriété basée sur le droit belge: gestion des propriétaires, lots, locataires, comptabilisation PCMN, import CODA, facturation avec clés de répartition, gestion des compteurs, interface bancaire complète avec lettrage, portail propriétaires avec documents.

## Architecture
- **Backend**: FastAPI + MongoDB (Motor async) on port 8001
- **Frontend**: React 19 + Tailwind CSS + Shadcn/UI on port 3000
- **Auth**: JWT httpOnly cookies, bcrypt password hashing, admin/owner roles
- **Database**: MongoDB (test_database)

## User Personas
1. **Syndic/Admin**: Full access to all management features
2. **Propriétaire/Owner**: View own documents and statements

## Core Requirements
- [x] JWT Authentication with roles
- [x] Propriétaires CRUD
- [x] Lots CRUD (with tantièmes/quotity)
- [x] Locataires CRUD
- [x] Plan Comptable PCMN (95 accounts pre-seeded)
- [x] Journaux Comptables (OD, Avances, Appels) with balance validation
- [x] Facturation avec clés de répartition
- [x] Gestion des compteurs (eau, chauffage, électricité) avec relevés
- [x] Interface bancaire (extraits, transactions, lettrage)
- [x] Import CODA (parser Belgian bank file format)
- [x] Gestion documents par catégories

## What's Been Implemented (May 2026)
- Complete backend with 9 route modules, CODA parser, PCMN data
- Full frontend with 10 pages, sidebar layout, Swiss/High-Contrast design
- 31 backend tests passing (100%)
- All 9 sidebar pages loading correctly in frontend

## Prioritized Backlog
### P0 (Critical)
- PDF generation for annual statements (décomptes annuels)
- Role-based access control on API routes (currently all routes open)

### P1 (Important)
- Owner portal (restricted view for propriétaires)
- Bilan comptable (balance sheet report)
- Payment encoding for supplier payments
- Real CODA file testing with sample files

### P2 (Nice to have)
- Email notifications for invoices/statements
- Automatic lettrage suggestions
- Multi-building support
- Audit trail / activity logs
- Dashboard charts with Recharts

## Next Tasks
1. Add auth middleware to all CRUD routes
2. Implement PDF décompte annuel generation
3. Create owner-restricted portal view
4. Add bilan comptable report

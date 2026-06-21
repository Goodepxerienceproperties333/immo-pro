"""Import Wizard module for Optipro/Sogis -> CoproManager migration.

Provides:
- CSV sniffing (encoding + separator)
- PDF text extraction (pdfplumber)
- Column mapping & validation
- Session-based rollback (every imported row tagged with import_session_id)
"""

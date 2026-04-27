"""
Shared SQLAlchemy extension instance for cdm_api.

All ORM models (src.models.*) and services should import `db` from here
to avoid circular imports with `app.py`.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

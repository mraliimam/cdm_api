"""
seed_db.py — Load CSV fixtures into the local SQLite database.

Usage
-----
    python seed_db.py              # seed all three tables
    python seed_db.py --table users
    python seed_db.py --table audio
    python seed_db.py --table workfiles

Load order: CC_AUDIO → AA_IAP_USERS → AA_IAP_WORKFILE
(IapWorkfile.ASSIGNED_USER_ID is a FK to AA_IAP_USERS.ID)

Behaviour
---------
- Rows whose primary key already exists in the DB are skipped (no duplicate inserts).
- 'NULL' strings in CSVs are treated as Python None.
- CSV columns not present on the ORM model are silently ignored.
- Inserts are committed in chunks of 500 rows so large CSVs don't OOM.
"""

import argparse
import os
import sys

import pandas as pd

# ── Bootstrap Flask app / db ────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from src.extensions import db
from src.models import AAIAPUSERS, AAIAPWORKFILE, CCAudio

# Backwards-compat aliases — older fixtures / external scripts may still use
# the legacy class names.
IapUser     = AAIAPUSERS
IapWorkfile = AAIAPWORKFILE
CdmAudio    = CCAudio

app = create_app()

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), 'schema')
CHUNK      = 500  # rows per commit


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Replace bare 'NULL' strings with NaN so pandas treats them as missing."""
    return df.replace({'NULL': None, 'null': None, '': None})


def _to_int(val):
    try:
        return int(float(val)) if val is not None and str(val).strip() != '' else None
    except (ValueError, TypeError):
        return None


def _to_float(val):
    try:
        return float(val) if val is not None and str(val).strip() != '' else None
    except (ValueError, TypeError):
        return None


def _to_str(val, maxlen=None):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if s in ('', 'nan', 'None'):
        return None
    return s[:maxlen] if maxlen else s


def _existing_ids(model) -> set:
    """Fetch the set of primary-key values already in the table."""
    pk_col = model.__mapper__.primary_key[0]
    rows   = db.session.query(pk_col).all()
    return {r[0] for r in rows}


def _insert_chunk(objects: list):
    db.session.bulk_save_objects(objects)
    db.session.commit()


def _progress(label: str, inserted: int, skipped: int, total: int):
    print(f'  [{label}] inserted {inserted}, skipped {skipped} / total {total}')


# ── Table seeders ─────────────────────────────────────────────────────────────

def seed_users():
    path = os.path.join(SCHEMA_DIR, 'iap_users.csv')
    print(f'\n→ Seeding AA_IAP_USERS from {path}')
    df = _clean(pd.read_csv(path, dtype=str))

    existing = _existing_ids(IapUser)
    inserted, skipped = 0, 0
    batch = []

    for _, row in df.iterrows():
        pk = _to_int(row.get('ID'))
        if pk in existing:
            skipped += 1
            continue

        obj = IapUser(
            ID                = pk,
            USERNAME          = _to_str(row.get('USERNAME'), 50),
            PASSWORD          = _to_str(row.get('PASSWORD'), 200),
            DATE_ADDED        = _to_str(row.get('DATE_ADDED'), 50),
            STATUS            = _to_str(row.get('STATUS'), 50),
            FIRST_NAME        = _to_str(row.get('FIRST_NAME'), 50),
            LAST_NAME         = _to_str(row.get('LAST_NAME'), 50),
            ACCESS_TO_PROD_FL = _to_str(row.get('ACCESS_TO_PROD_FL'), 1),
            FOLDER_SETUP_FL   = _to_str(row.get('FOLDER_SETUP_FL'), 1),
            USER_STAGE        = _to_str(row.get('USER_STAGE'), 50),
            SESSION_STATUS    = _to_str(row.get('SESSION_STATUS'), 50),
            LAST_ACTIVITY     = _to_str(row.get('LAST_ACTIVITY'), 50),
            LOG_IN_DTS        = _to_str(row.get('LOG_IN_DTS'), 50),
            PAYMENT_RATIO     = _to_float(row.get('PAYMENT_RATIO')),
            # CDM columns — not in CSV; remain NULL until set via the API
            CDM_IS_ACTIVE_FL  = 'Y',
        )
        batch.append(obj)
        existing.add(pk)
        inserted += 1

        if len(batch) >= CHUNK:
            _insert_chunk(batch)
            batch = []

    if batch:
        _insert_chunk(batch)

    _progress('AA_IAP_USERS', inserted, skipped, inserted + skipped)


def seed_audio():
    path = os.path.join(SCHEMA_DIR, 'cc_audio.csv')
    print(f'\n→ Seeding CC_AUDIO from {path}')
    df = _clean(pd.read_csv(path, dtype=str))

    existing = _existing_ids(CdmAudio)
    inserted, skipped = 0, 0
    batch = []

    for _, row in df.iterrows():
        pk = _to_int(row.get('ID'))
        if pk in existing:
            skipped += 1
            continue

        obj = CdmAudio(
            ID                           = pk,
            FILEPATH                     = _to_str(row.get('FILEPATH'), 400),
            AUDIO_KEY                    = _to_str(row.get('AUDIO_KEY'), 250),
            AUDIO_SOURCE                 = _to_str(row.get('AUDIO_SOURCE'), 50),
            DURATION                     = _to_str(row.get('DURATION')),
            AUDIO_LENGTH                 = _to_float(row.get('AUDIO_LENGTH')),
            AUDIO_TYPE                   = _to_str(row.get('AUDIO_TYPE'), 50),
            STATUS                       = _to_str(row.get('STATUS'), 50),
            MISTAKE_LEVEL                = _to_str(row.get('MISTAKE_LEVEL'), 50),
            BACKGROUND_NOISE_LEVEL       = _to_str(row.get('BACKGROUND_NOISE_LEVEL'), 50),
            REPEATS_PAUSES_STUTTER_LEVEL = _to_str(row.get('REPEATS_PAUSES_STUTTER_LEVEL'), 100),
            AUDIO_ISSUES_LEVEL           = _to_str(row.get('AUDIO_ISSUES_LEVEL'), 50),
            RECITATION_SPEED             = _to_str(row.get('RECITATION_SPEED'), 50),
            VOICE_PITCH                  = _to_str(row.get('VOICE_PITCH'), 50),
            VOICE_CLARITY                = _to_str(row.get('VOICE_CLARITY'), 50),
            VOICE_LEVEL                  = _to_str(row.get('VOICE_LEVEL'), 50),
            WHISPER_FL                   = _to_str(row.get('WHISPER_FL'), 1),
            AUDIO_CLIPPED_BEG_FL         = _to_str(row.get('AUDIO_CLIPPED_BEG_FL'), 1),
            AUDIO_CLIPPED_END_FL         = _to_str(row.get('AUDIO_CLIPPED_END_FL'), 1),
            SCORE                        = _to_float(row.get('SCORE')),
            SURAH_SCORE                  = _to_float(row.get('SURAH_SCORE')),
            PROFILE_SCORE                = _to_float(row.get('PROFILE_SCORE')),
            META_SCORE                   = _to_float(row.get('META_SCORE')),
            UPLOADER_ID                  = _to_int(row.get('UPLOADER_ID')),
            WEBSITE_USER_ID              = _to_int(row.get('WEBSITE_USER_ID')),
            UNKNOWN_USER_ID_1            = _to_int(row.get('UNKNOWN_USER_ID_1')),
            STUDENT_ID_1                 = _to_int(row.get('STUDENT_ID_1')),
            SUPERVISOR_ID                = _to_int(row.get('SUPERVISOR_ID')),
            ETL_ADD_DTS                  = _to_str(row.get('ETL_ADD_DTS'), 50),
            ETL_ROW_PROCESS_DTS          = _to_str(row.get('ETL_ROW_PROCESS_DTS'), 50),
            ETL_PROCESSED_FL             = _to_str(row.get('ETL_PROCESSED_FL'), 1),
        )
        batch.append(obj)
        existing.add(pk)
        inserted += 1

        if len(batch) >= CHUNK:
            _insert_chunk(batch)
            batch = []

    if batch:
        _insert_chunk(batch)

    _progress('CC_AUDIO', inserted, skipped, inserted + skipped)


def seed_workfiles():
    path = os.path.join(SCHEMA_DIR, 'iap_workfiles.csv')
    print(f'\n→ Seeding AA_IAP_WORKFILE from {path}')
    df = _clean(pd.read_csv(path, dtype=str))

    existing = _existing_ids(IapWorkfile)
    inserted, skipped = 0, 0
    batch = []

    for _, row in df.iterrows():
        pk = _to_int(row.get('ID'))
        if pk in existing:
            skipped += 1
            continue

        obj = IapWorkfile(
            ID                    = pk,
            WORKFILE_NAME         = _to_str(row.get('WORKFILE_NAME'), 50),
            WORKFILE_STATUS       = _to_str(row.get('WORKFILE_STATUS'), 50),
            ASSIGNED_USER_ID      = _to_int(row.get('ASSIGNED_USER_ID')),
            AUDIO_FILEPATH        = _to_str(row.get('AUDIO_FILEPATH'), 50),
            AUDIO_STATUS          = _to_str(row.get('AUDIO_STATUS'), 50),
            AUDIO_KEY             = _to_str(row.get('AUDIO_KEY'), 200),
            FILESAVE_FILEPATH     = _to_str(row.get('FILESAVE_FILEPATH'), 50),
            FILESAVE_STATUS       = _to_str(row.get('FILESAVE_STATUS'), 50),
            MODELPRED_FILEPATH    = _to_str(row.get('MODELPRED_FILEPATH'), 50),
            MODELPRED_STATUS      = _to_str(row.get('MODELPRED_STATUS'), 50),
            STAGE                 = _to_str(row.get('STAGE'), 50),
            CC_AUDIO_ID           = _to_str(row.get('CC_AUDIO_ID'), 50),
            AA_RECORD_ID          = _to_str(row.get('AA_RECORD_ID'), 50),
            TEST_STATIC_ID        = _to_str(row.get('TEST_STATIC_ID'), 50),
            LAST_MOVED_DT         = _to_str(row.get('LAST_MOVED_DT'), 50),
            LAST_MOVED_BY         = _to_str(row.get('LAST_MOVED_BY'), 50),
            CURR_ROW_FL           = _to_str(row.get('CURR_ROW_FL'), 1),
            TO_DO_MOVED_DTS       = _to_str(row.get('TO_DO_MOVED_DTS'), 50),
            IN_PROGRESS_MOVED_DTS = _to_str(row.get('IN_PROGRESS_MOVED_DTS'), 50),
            COMPLETED_MOVED_DTS   = _to_str(row.get('COMPLETED_MOVED_DTS'), 50),
            USER_STAGE            = _to_str(row.get('USER_STAGE'), 50),
            REVIEW_FL             = _to_str(row.get('REVIEW_FL'), 1),
            IS_FILE_UPLOAD        = _to_str(row.get('IS_FILE_UPLOAD'), 1),
        )
        batch.append(obj)
        existing.add(pk)
        inserted += 1

        if len(batch) >= CHUNK:
            _insert_chunk(batch)
            batch = []

    if batch:
        _insert_chunk(batch)

    _progress('AA_IAP_WORKFILE', inserted, skipped, inserted + skipped)


# ── Entry point ───────────────────────────────────────────────────────────────

SEEDERS = {
    'audio':     seed_audio,
    'users':     seed_users,
    'workfiles': seed_workfiles,
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Seed local SQLite DB from CSV fixtures.')
    parser.add_argument(
        '--table',
        choices=list(SEEDERS.keys()),
        default=None,
        help='Seed a single table (default: all tables in dependency order).',
    )
    args = parser.parse_args()

    with app.app_context():
        if args.table:
            SEEDERS[args.table]()
        else:
            # Dependency order: audio and users have no FK deps; workfiles refs users
            seed_audio()
            seed_users()
            seed_workfiles()

    print('\n✓ Seeding complete.')

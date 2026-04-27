import os
from dotenv import load_dotenv

load_dotenv()

_ENV = os.getenv('ENV', 'production').lower()
_LOCAL = _ENV == 'local'


class DatabaseConfig:
    """
    Database configuration.

    ENV=local   → SQLite file at cdm_api/local.db  (no credentials needed)
    ENV=<other> → MSSQL via pymssql (requires DBUSERNAME, DBPASSWORD, SERVER,
                  DBPORT, DBNAME in the environment / .env file)
    """

    IS_LOCAL = _LOCAL

    if _LOCAL:
        SQLALCHEMY_DATABASE_URI = 'sqlite:///local.db'
        SQLALCHEMY_ENGINE_OPTIONS = {}
    else:
        DBUSERNAME = os.getenv('DBUSERNAME')
        DBPASSWORD = os.getenv('DBPASSWORD')
        SERVER     = os.getenv('SERVER')
        DBPORT     = os.getenv('DBPORT')
        DBNAME     = os.getenv('DBNAME')

        SQLALCHEMY_DATABASE_URI = (
            f"mssql+pymssql://{DBUSERNAME}:{DBPASSWORD}@{SERVER}:{DBPORT}/{DBNAME}"
        )
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_recycle": 1800,
            "pool_size":    10,
            "max_overflow": 20,
        }

    SQLALCHEMY_TRACK_MODIFICATIONS = False

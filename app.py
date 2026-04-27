from flask import Flask
from flask_cors import CORS

from configs import DatabaseConfig
from src.extensions import db
from src import models  # noqa: F401  -- ensures all ORM tables are registered with `db`
from src.api.cdm_allocation import cdm_allocation_api


def create_app():
    app = Flask(__name__)
    app.config.from_object(DatabaseConfig)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

    CORS(app)
    db.init_app(app)

    app.register_blueprint(cdm_allocation_api)

    if DatabaseConfig.IS_LOCAL:
        with app.app_context():
            db.create_all()
            print('[local] SQLite tables created → local.db')

    return app


if __name__ == '__main__':
    application = create_app()
    is_local = DatabaseConfig.IS_LOCAL
    application.run(debug=is_local, host='0.0.0.0', port=5001)

import os
import json
from flask import Flask

from .routes import bp


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='supersecret',
    )

    # load app sepcified configuration
    if test_config is None:
        # load the instance config, if it exists, when not testing
        app.config.from_file('config.json', load=json.load)
    else:
        # load the test config if passed in
        app.config.from_mapping(test_config)
    # ensure the instance folder exists
    try: os.makedirs(app.instance_path)
    except OSError: pass
    setup_app(app)
    return app

def setup_app(app):
    # Create tables if they do not exist already
    app.register_blueprint(bp, url_prefix='')

@bp.cli.command("initdb")
def initdb():
    pass
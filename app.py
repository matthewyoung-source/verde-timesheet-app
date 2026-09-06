import os
from flask import Flask, send_from_directory, abort, redirect, url_for
from flask_login import login_required, current_user

from extensions import db, login_manager, scheduler

from config import Config
from models import User, Expense


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["PDF_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from auth import auth_bp
    from contractor import contractor_bp
    from admin import admin_bp
    from client import client_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(contractor_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(client_bp)

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    @app.route("/uploads/<filename>")
    @login_required
    def uploaded_file(filename):
        # Only the admin, or the contractor who owns the expense this photo
        # belongs to, may view a receipt image.
        if not current_user.is_admin():
            expense = Expense.query.filter_by(photo_filename=filename).first()
            if not expense or expense.assignment.contractor_id != current_user.id:
                abort(403)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    with app.app_context():
        db.create_all()
        from db_upgrade import run as run_db_upgrade
        run_db_upgrade(db)

    from scheduler_jobs import register_jobs
    register_jobs(app, scheduler)
    if not scheduler.running:
        scheduler.start()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)

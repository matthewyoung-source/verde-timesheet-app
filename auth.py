from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db
from models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.is_admin() else "contractor.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.active and user.check_password(password):
            login_user(user)
            return redirect(url_for("admin.dashboard" if user.is_admin() else "contractor.dashboard"))

        flash("Incorrect email or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_email = request.form.get("email", "").strip().lower()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not current_user.check_password(current_password):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("auth.account"))

        if not new_email:
            flash("Email cannot be blank.", "error")
            return redirect(url_for("auth.account"))

        existing = User.query.filter_by(email=new_email).first()
        if existing and existing.id != current_user.id:
            flash("Another account already uses that email.", "error")
            return redirect(url_for("auth.account"))

        if new_password or confirm_password:
            if new_password != confirm_password:
                flash("New password and confirmation do not match.", "error")
                return redirect(url_for("auth.account"))
            if len(new_password) < 8:
                flash("New password must be at least 8 characters.", "error")
                return redirect(url_for("auth.account"))
            current_user.set_password(new_password)

        current_user.email = new_email
        db.session.commit()
        flash("Account updated.", "success")
        return redirect(url_for("auth.account"))

    return render_template("account.html")

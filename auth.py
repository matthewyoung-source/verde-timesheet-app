from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db, limiter
from models import User
import notifications

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"], methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(current_user.home_endpoint()))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and user.active and user.check_password(password):
            login_user(user)
            return redirect(url_for(user.home_endpoint()))

        flash("Incorrect email or password.", "error")

    return render_template("login.html")


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        # Always show the same message, whether or not the email matched --
        # don't reveal which addresses have accounts.
        if user and user.active:
            token = user.get_reset_token(current_app.config["SECRET_KEY"])
            reset_url = f"{current_app.config['APP_BASE_URL']}/reset-password/{token}"
            sent = notifications.send_email(
                current_app,
                user.email,
                "Reset your Verde Solutions password",
                (
                    f"Hi {user.name},\n\n"
                    "Someone (hopefully you) asked to reset your password.\n"
                    f"Click here to set a new one (link expires in 2 hours):\n{reset_url}\n\n"
                    "If you didn't request this, you can ignore this email."
                ),
            )
            if not sent:
                current_app.logger.info(
                    "Password reset requested for %s but email isn't configured yet -- "
                    "link: %s", user.email, reset_url,
                )

        flash("If that email has an account, a reset link has been sent.", "success")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = User.verify_reset_token(token, current_app.config["SECRET_KEY"])
    if not user:
        flash("That reset link is invalid or has expired. Request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "error")
        elif len(new_password) < 8:
            flash("New password must be at least 8 characters.", "error")
        else:
            user.set_password(new_password)
            db.session.commit()
            flash("Password updated -- you can log in now.", "success")
            return redirect(url_for("auth.login"))

    return render_template("reset_password.html", token=token)


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

import getpass

from werkzeug.security import generate_password_hash

from app import create_app
from app.shared.db import db
from app.shared.models.core import User


def create_admin():
    email = input("Admin email: ").strip().lower()
    password = getpass.getpass("Password: ")
    with create_app().app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            print("User already exists")
            return
        admin = User(role="admin", email=email, password_hash=generate_password_hash(password))  # type: ignore[call-arg]
        db.session.add(admin)
        db.session.commit()
        print("Admin created")


def reset_admin_password():
    email = input("Admin email to reset: ").strip().lower()
    password = getpass.getpass("New password: ")
    with create_app().app_context():
        admin = User.query.filter_by(email=email, role="admin").first()
        if not admin:
            print("Admin not found")
            return
        admin.password_hash = generate_password_hash(password)
        db.session.commit()
        print("Admin password updated")


if __name__ == "__main__":
    if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "reset":
        reset_admin_password()
    else:
        create_admin()

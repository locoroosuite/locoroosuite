from flask import render_template, request, redirect, url_for, session

from app.shared.db import db
from app.shared.models.core import User, CustomerAccount, ManagerDomain
from app.shared.audit import log_audit
from app.shared.auth import require_role
from app.modules.mail.services.cache import purge_cache
from app.admin import manager_bp


@manager_bp.route("/")
@require_role("manager")
def dashboard():
    manager_id = session.get("user_id")
    domain_ids = [link.domain_id for link in ManagerDomain.query.filter_by(manager_id=manager_id).all()]
    customer_ids = [row.customer_id for row in CustomerAccount.query.filter(CustomerAccount.domain_id.in_(domain_ids)).all()]
    customers = User.query.filter(User.id.in_(customer_ids)).all() if customer_ids else []
    return render_template("manager/dashboard.html", customers=customers)


@manager_bp.route("/customers/new", methods=["POST"])
@require_role("manager")
def create_customer():
    email = request.form.get("email", "").strip().lower()
    customer = User(role="customer", email=email)
    db.session.add(customer)
    db.session.commit()

    log_audit(session.get("user_id"), "manager", "customer_create", email, request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("manager.dashboard"))


@manager_bp.route("/customers/<int:customer_id>/toggle", methods=["POST"])
@require_role("manager")
def toggle_customer(customer_id):
    customer = db.get_or_404(User, customer_id)
    customer.is_active = not customer.is_active
    db.session.commit()
    log_audit(session.get("user_id"), "manager", "customer_toggle", f"customer={customer.email},active={customer.is_active}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("manager.dashboard"))


@manager_bp.route("/customers/<int:customer_id>/purge", methods=["POST"])
@require_role("manager")
def purge_customer(customer_id):
    accounts = CustomerAccount.query.filter_by(customer_id=customer_id).all()
    for account in accounts:
        purge_cache(account.cache_db_path)
        account.cache_db_path = None
    db.session.commit()
    log_audit(session.get("user_id"), "manager", "customer_cache_reset", f"customer={customer_id}", request.remote_addr, request.headers.get("User-Agent"))
    return redirect(url_for("manager.dashboard"))

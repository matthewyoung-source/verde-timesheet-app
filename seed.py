"""One-time setup script: creates Matthew's admin login, plus a sample
client, contractor, and assignment so the app can be clicked through and
tested end to end. Safe to re-run -- skips anything that already exists."""

from datetime import date

from app import create_app
from extensions import db
from models import User, Client, Assignment

app = create_app()

with app.app_context():
    if not User.query.filter_by(email="matthew@verdesolutions.co.uk").first():
        admin = User(name="Matthew Young", email="matthew@verdesolutions.co.uk", role="admin")
        admin.set_password("changeme123")
        db.session.add(admin)
        print("Created admin login: matthew@verdesolutions.co.uk / changeme123")
    else:
        print("Admin already exists.")

    client = Client.query.filter_by(name="Sample Solar Client").first()
    if not client:
        client = Client(name="Sample Solar Client", billing_email="ap@sampleclient.com")
        db.session.add(client)
        db.session.flush()
        print("Created sample client: Sample Solar Client")

    contractor = User.query.filter_by(email="contractor@example.com").first()
    if not contractor:
        contractor = User(name="Jamie Test", email="contractor@example.com", role="contractor")
        contractor.set_password("changeme123")
        db.session.add(contractor)
        db.session.flush()
        print("Created test contractor login: contractor@example.com / changeme123")

    if contractor.id and client.id:
        existing_assignment = Assignment.query.filter_by(
            contractor_id=contractor.id, client_id=client.id
        ).first()
        if not existing_assignment:
            db.session.add(
                Assignment(
                    contractor_id=contractor.id,
                    client_id=client.id,
                    billing_rate=65.00,
                    role_title="Site Electrician",
                )
            )
            print("Created sample assignment at $65.00/hr")

    db.session.commit()
    print("Seed complete.")

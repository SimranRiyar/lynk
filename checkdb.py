from app import create_app
from app.models import User
import os

app = create_app()
with app.app_context():
    print("DATABASE_URL:", os.environ.get("DATABASE_URL", "NOT SET")[:50])
    users = User.query.all()
    print(f"Total users: {len(users)}")
    for u in users:
        print(f"  - {u.email} | {u.username}")
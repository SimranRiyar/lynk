from app import create_app
from app.models import User

app = create_app()
with app.app_context():
    users = User.query.all()
    print(f"Total users: {len(users)}")
    for u in users:
        print(f"Email: {u.email} | Username: {u.username} | Verified: {u.is_verified}")
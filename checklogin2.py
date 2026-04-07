from app import create_app
from app.models import User
from werkzeug.security import check_password_hash

app = create_app()
with app.app_context():
    # Try finding by email exactly as typed
    email = 'simrankaurriyar04@gmail.com'
    user = User.query.filter_by(email=email).first()
    print(f"User found: {user is not None}")
    if user:
        print(f"Email in DB: '{user.email}'")
        print(f"Email match: {user.email == email}")
        print(f"Password check: {check_password_hash(user.password, 'newpassword123')}")
        print(f"is_verified: {user.is_verified}")
        print(f"is_banned: {user.is_banned}")
        print(f"is_locked: {user.is_locked()}")
        print(f"failed_attempts: {user.failed_login_attempts}")
from app import create_app
from app.models import User
from werkzeug.security import check_password_hash

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    print("Found:", user.username)
    print("Password OK:", check_password_hash(user.password, 'Test1234!'))
    print("Verified:", user.is_verified)
    print("Banned:", user.is_banned)
    print("Locked:", user.is_locked())
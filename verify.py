from app import create_app
from app.extensions import db
from app.models import User

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    if user:
        user.is_verified = True
        db.session.commit()
        print('User verified! You can now log in.')
    else:
        print('User not found. Please register first on the website.')
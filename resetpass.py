from app import create_app
from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    if user:
        user.password = generate_password_hash('newpassword123')
        db.session.commit()
        print('Password reset successfully!')
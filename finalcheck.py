from app import create_app
from app.models import User
from werkzeug.security import check_password_hash, generate_password_hash

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    
    # Set a super simple password
    user.password = generate_password_hash('abc123')
    from app.extensions import db
    db.session.commit()
    
    # Verify immediately
    user2 = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    print("Password 'abc123' works:", check_password_hash(user2.password, 'abc123'))
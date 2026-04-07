from app import create_app
from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash, check_password_hash

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    user.password = generate_password_hash('abc123')
    db.session.commit()
    
    # Verify immediately
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    ok = check_password_hash(user.password, 'abc123')
    print(f'Password set and verified: {ok}')
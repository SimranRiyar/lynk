from app import create_app
from app.models import User
from werkzeug.security import check_password_hash, generate_password_hash

app = create_app()
with app.app_context():
    # Find user
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    if not user:
        print("User NOT found by email")
        user = User.query.filter_by(username='Simran').first()
        if user:
            print(f"Found by username: {user.username}, email: {user.email}")
    else:
        print(f"Found user: {user.username}")
    
    if user:
        # Reset password to something known
        user.password = generate_password_hash('Test1234!')
        from app.extensions import db
        db.session.commit()
        
        # Verify it works
        result = check_password_hash(user.password, 'Test1234!')
        print(f"Password check works: {result}")
        print(f"Is verified: {user.is_verified}")
        print(f"Is active: {getattr(user, 'is_active', 'NO SUCH FIELD')}")
        print(f"Is paused: {getattr(user, 'is_paused', 'NO SUCH FIELD')}")
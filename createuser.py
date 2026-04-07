import os
os.environ['DATABASE_URL'] = 'postgresql://postgres:IBdPdufupYjoLVasLdDvOCUyygAIBuCt@junction.proxy.rlwy.net:45787/railway'

from app import create_app
from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    db.create_all()
    
    # Check existing users
    users = User.query.all()
    print(f"Users in production DB: {len(users)}")
    for u in users:
        print(f"  - {u.email} | {u.username}")
    
    # Create your account
    existing = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    if existing:
        existing.password = generate_password_hash('Lynk1234!')
        existing.is_verified = True
        db.session.commit()
        print("Password reset!")
    else:
        user = User(
            username='Simran',
            email='simrankaurriyar04@gmail.com',
            password=generate_password_hash('Lynk1234!'),
            is_verified=True
        )
        db.session.add(user)
        db.session.commit()
        print("User created!")
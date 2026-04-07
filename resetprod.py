import os
os.environ['DATABASE_URL'] = 'postgresql://postgres:IBdPdufupYjoLVasLdDvOCUyygAIBuCt@junction.proxy.rlwy.net:45787/railway'

from app import create_app
from app.extensions import db
from app.models import User
from werkzeug.security import generate_password_hash, check_password_hash

app = create_app()
with app.app_context():
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    user.password = generate_password_hash('Lynk1234!')
    db.session.commit()
    user = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    print("Password verified:", check_password_hash(user.password, 'Lynk1234!'))
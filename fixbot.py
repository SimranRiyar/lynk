import os
os.environ['DATABASE_URL'] = 'postgresql://postgres:IBdPdufupYjoLVasLdDvOCUyygAIBuCt@junction.proxy.rlwy.net:45787/railway'

from app import create_app
from app.extensions import db
from app.models import User, Follow

app = create_app()
with app.app_context():
    simran = User.query.filter_by(email='simrankaurriyar04@gmail.com').first()
    bot = User.query.filter_by(username='lynk_ai').first()
    
    # Make them follow each other
    f1 = Follow.query.filter_by(follower_id=simran.id, followed_id=bot.id).first()
    if not f1:
        db.session.add(Follow(follower_id=simran.id, followed_id=bot.id, status='accepted'))
    
    f2 = Follow.query.filter_by(follower_id=bot.id, followed_id=simran.id).first()
    if not f2:
        db.session.add(Follow(follower_id=bot.id, followed_id=simran.id, status='accepted'))
    
    db.session.commit()
    print("Done! You can now message lynk_ai")
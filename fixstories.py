import os
os.environ['DATABASE_URL'] = 'postgresql://postgres:IBdPdufupYjoLVasLdDvOCUyygAIBuCt@junction.proxy.rlwy.net:45787/railway'

from app import create_app
from app.extensions import db
from app.models import Story, StoryView
from datetime import datetime

app = create_app()
with app.app_context():
    expired = Story.query.filter(Story.expires_at <= datetime.utcnow()).all()
    print(f"Found {len(expired)} expired stories")
    for story in expired:
        StoryView.query.filter_by(story_id=story.id).delete()
        db.session.delete(story)
    db.session.commit()
    print("Done!")
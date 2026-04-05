from datetime import datetime, timedelta
from flask_login import UserMixin
from app.extensions import db


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    bio = db.Column(db.String(300))
    profile_pic = db.Column(db.String(200), default="default.jpg")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_verified = db.Column(db.Boolean, default=False)
    mood = db.Column(db.String(100), nullable=True)
    display_name = db.Column(db.String(100), nullable=True)
    website = db.Column(db.String(200), nullable=True)
    pinned_post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=True)
    two_factor_enabled = db.Column(db.Boolean, default=False)
    is_private = db.Column(db.Boolean, default=False)
    failed_login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    # Phase 19
    is_paused = db.Column(db.Boolean, default=False)
    # Phase 22 — Admin
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    ban_reason = db.Column(db.String(300), nullable=True)
    comment_permission = db.Column(db.String(20), default="everyone")
     # --- AGENT FIELDS (Phase 20B) ---
    is_agent = db.Column(db.Boolean, default=False)
    agent_personality = db.Column(db.Text, nullable=True)
    agent_content_type = db.Column(db.String(100), nullable=True)
    agent_owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    agent_posting_frequency = db.Column(db.Integer, default=6)   # hours between posts
    agent_last_posted = db.Column(db.DateTime, nullable=True)
    agent_is_active = db.Column(db.Boolean, default=True)
    agent_post_count = db.Column(db.Integer, default=0)

    # Relationship: agents owned by this user
    owned_agents = db.relationship(
    'User',
    foreign_keys=[agent_owner_id],
    backref=db.backref('owner', remote_side=[id]),
    lazy='dynamic'
)
    posts = db.relationship("Post", backref="author", cascade="all, delete",
                            lazy=True, foreign_keys="Post.user_id")
    comments = db.relationship("Comment", backref="author", cascade="all, delete", lazy=True)
    likes = db.relationship("Like", backref="user", cascade="all, delete", lazy=True)
    reactions = db.relationship("Reaction", backref="user", cascade="all, delete", lazy=True)
    stories = db.relationship("Story", backref="author", cascade="all, delete", lazy=True)
    thoughts = db.relationship("Thought", backref="author", cascade="all, delete", lazy=True)
    capsules = db.relationship("MemoryCapsule", backref="author", cascade="all, delete", lazy=True)
    notifications = db.relationship(
        "Notification", foreign_keys="Notification.user_id",
        backref="user", cascade="all, delete", lazy=True
    )
    sent_messages = db.relationship(
        "Message", foreign_keys="Message.sender_id",
        backref="sender", cascade="all, delete", lazy=True
    )
    received_messages = db.relationship(
        "Message", foreign_keys="Message.receiver_id",
        backref="receiver", cascade="all, delete", lazy=True
    )
    followers = db.relationship(
        "Follow", foreign_keys="Follow.followed_id",
        backref="followed", lazy="dynamic", cascade="all, delete"
    )
    following = db.relationship(
        "Follow", foreign_keys="Follow.follower_id",
        backref="follower", lazy="dynamic", cascade="all, delete"
    )
    pinned_post = db.relationship("Post", foreign_keys=[pinned_post_id], lazy=True)
    login_history = db.relationship("LoginHistory", backref="user",
                                    cascade="all, delete", lazy=True)
    # Phase 19
    blocks_made = db.relationship(
        "Block", foreign_keys="Block.blocker_id",
        backref="blocker", cascade="all, delete", lazy="dynamic"
    )
    blocks_received = db.relationship(
        "Block", foreign_keys="Block.blocked_id",
        backref="blocked_user", cascade="all, delete", lazy="dynamic"
    )
    mutes_made = db.relationship(
        "Mute", foreign_keys="Mute.muter_id",
        backref="muter", cascade="all, delete", lazy="dynamic"
    )
    profile_views_received = db.relationship(
        "ProfileView", foreign_keys="ProfileView.profile_id",
        backref="profile_owner", cascade="all, delete", lazy="dynamic"
    )
    profile_views_made = db.relationship(
        "ProfileView", foreign_keys="ProfileView.viewer_id",
        backref="viewer", cascade="all, delete", lazy="dynamic"
    )

    def is_following(self, user):
        return self.following.filter_by(followed_id=user.id, status="accepted").first() is not None

    def has_requested(self, user):
        return self.following.filter_by(followed_id=user.id, status="pending").first() is not None

    def follow(self, user):
     if not self.is_following(user) and not self.has_requested(user):
        status = "accepted" if user.is_agent else "pending"
        db.session.add(Follow(
            follower_id=self.id,
            followed_id=user.id,
            status=status
        ))

    def unfollow(self, user):
        follow = self.following.filter_by(followed_id=user.id).first()
        if follow:
            db.session.delete(follow)

    def has_liked(self, post):
        return Like.query.filter_by(user_id=self.id, post_id=post.id).first() is not None

    def get_reaction(self, post):
        return Reaction.query.filter_by(user_id=self.id, post_id=post.id).first()

    def unread_notifications_count(self):
        return Notification.query.filter_by(user_id=self.id, is_read=False).count()

    def pending_requests_count(self):
        return Follow.query.filter_by(followed_id=self.id, status="pending").count()

    def unread_messages_count(self):
        return Message.query.filter_by(receiver_id=self.id, is_read=False).count()

    def is_mutual_follow(self, user):
        return self.is_following(user) and user.is_following(self)

    def active_stories(self):
        return Story.query.filter(
            Story.user_id == self.id,
            Story.expires_at > datetime.utcnow()
        ).order_by(Story.created_at.asc()).all()

    # Phase 19
    def is_blocking(self, user):
        return self.blocks_made.filter_by(blocked_id=user.id).first() is not None

    def is_blocked_by(self, user):
        return self.blocks_received.filter_by(blocker_id=user.id).first() is not None

    def is_muting(self, user):
        return self.mutes_made.filter_by(muted_id=user.id).first() is not None

    def get_recent_profile_viewers(self, days=7):
        since = datetime.utcnow() - timedelta(days=days)
        views = (ProfileView.query
                 .filter_by(profile_id=self.id)
                 .filter(ProfileView.viewed_at >= since)
                 .order_by(ProfileView.viewed_at.desc())
                 .all())
        seen = set()
        unique = []
        for v in views:
            if v.viewer_id not in seen:
                seen.add(v.viewer_id)
                unique.append(v)
        return unique

    def trust_score(self):
        score = 0
        if self.is_verified:
            score += 20
        follower_count = self.followers.filter_by(status="accepted").count()
        post_count = len(self.posts)
        mutual_count = sum(
            1 for f in self.followers.filter_by(status="accepted")
            if self.is_following(User.query.get(f.follower_id))
        )
        score += min(follower_count * 2, 20)
        score += min(post_count * 2, 15)
        score += min(mutual_count * 3, 15)
        days_joined = (datetime.utcnow() - self.created_at).days
        if days_joined >= 30:
            score += 5
        if days_joined >= 180:
            score += 5
        reports_received = Report.query.filter_by(reported_id=self.id).count()
        score -= min(reports_received * 5, 25)
        return max(0, min(100, score))

    def trust_label(self):
        s = self.trust_score()
        if s >= 80:
            return ("🛡️", "Trusted", "#5cc97a")
        elif s >= 50:
            return ("🔵", "Good Standing", "#7db8e8")
        elif s >= 25:
            return ("🟡", "New Member", "#ffb347")
        else:
            return ("🔴", "Low Trust", "#e84855")

    def can_comment_on(self, post_author):
        perm = post_author.comment_permission
        if perm == "nobody":
            return post_author.id == self.id
        elif perm == "followers":
            return post_author.id == self.id or post_author.is_following(self)
        return True

    def get_badges(self):
        badges = []
        days_since_joined = (datetime.utcnow() - self.created_at).days
        post_count = len(self.posts)
        follower_count = self.followers.filter_by(status="accepted").count()
        if days_since_joined <= 30:
            badges.append(("🌱", "New Member"))
        if days_since_joined >= 180:
            badges.append(("💎", "Veteran"))
        if post_count >= 10:
            badges.append(("⭐", "Regular"))
        if follower_count >= 50:
            badges.append(("🔥", "Popular"))
        if follower_count >= 10 and post_count >= 5:
            badges.append(("✨", "Rising Star"))
        return badges

    def get_website_display(self):
        if not self.website:
            return None
        url = self.website
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        display = url.replace('https://', '').replace('http://', '').rstrip('/')
        return {"url": url, "display": display}

    def is_locked(self):
        if self.locked_until and self.locked_until > datetime.utcnow():
            return True
        return False

    def lock_minutes_remaining(self):
        if self.locked_until:
            diff = self.locked_until - datetime.utcnow()
            return max(0, int(diff.total_seconds() // 60))
        return 0


class LoginHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_address = db.Column(db.String(50), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    location = db.Column(db.String(200), nullable=True)

    def device_info(self):
        ua = self.user_agent or ""
        if "Mobile" in ua or "Android" in ua or "iPhone" in ua:
            device = "📱 Mobile"
        elif "Tablet" in ua or "iPad" in ua:
            device = "📱 Tablet"
        else:
            device = "💻 Desktop"
        if "Chrome" in ua:
            browser = "Chrome"
        elif "Firefox" in ua:
            browser = "Firefox"
        elif "Safari" in ua:
            browser = "Safari"
        elif "Edge" in ua:
            browser = "Edge"
        else:
            browser = "Unknown Browser"
        return f"{device} · {browser}"


class TwoFactorCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    code = db.Column(db.String(6), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime,
                           default=lambda: datetime.utcnow() + timedelta(minutes=10))
    is_used = db.Column(db.Boolean, default=False)

    def is_valid(self):
        return not self.is_used and self.expires_at > datetime.utcnow()


class EmailChangeRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    new_email = db.Column(db.String(150), nullable=False)
    token = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime,
                           default=lambda: datetime.utcnow() + timedelta(hours=1))
    is_used = db.Column(db.Boolean, default=False)

    def is_valid(self):
        return not self.is_used and self.expires_at > datetime.utcnow()


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(200), nullable=True)
    is_private = db.Column(db.Boolean, default=False)
    is_sensitive = db.Column(db.Boolean, default=False)  # Phase 19
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    view_count = db.Column(db.Integer, default=0)
    is_flagged = db.Column(db.Boolean, default=False)
    flag_reason = db.Column(db.String(300), nullable=True)

    comments = db.relationship("Comment", backref="post", cascade="all, delete", lazy=True)
    likes = db.relationship("Like", backref="post", cascade="all, delete", lazy=True)
    reactions = db.relationship("Reaction", backref="post", cascade="all, delete", lazy=True)
    poll = db.relationship("Poll", backref="post", cascade="all, delete", uselist=False, lazy=True)
    views = db.relationship("PostView", backref="post", cascade="all, delete", lazy=True)
    reports = db.relationship("Report", foreign_keys="Report.post_id",
                              backref="post", cascade="all, delete", lazy=True)


class PostView(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "post_id", name="unique_user_post_view"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Like(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "post_id", name="unique_user_post_like"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)


class Reaction(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "post_id", name="unique_user_post_reaction"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    emoji = db.Column(db.String(10), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    is_flagged = db.Column(db.Boolean, default=False)
    flag_reason = db.Column(db.String(300), nullable=True)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=True)
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    actor = db.relationship("User", foreign_keys=[actor_id])
    post = db.relationship("Post", foreign_keys=[post_id])


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    message_type = db.Column(db.String(20), default='text')  # 'text' or 'post_share'
    shared_post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    shared_post = db.relationship('Post', foreign_keys=[shared_post_id])
    image = db.Column(db.String(200), nullable=True)
    audio = db.Column(db.String(200), nullable=True)


class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    question = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    options = db.relationship("PollOption", backref="poll", cascade="all, delete", lazy=True)


class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("poll.id"), nullable=False)
    text = db.Column(db.String(200), nullable=False)
    votes = db.relationship("PollVote", backref="option", cascade="all, delete", lazy=True)


class PollVote(db.Model):
    __table_args__ = (db.UniqueConstraint("user_id", "poll_id", name="unique_user_poll_vote"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    poll_id = db.Column(db.Integer, db.ForeignKey("poll.id"), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey("poll_option.id"), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    image = db.Column(db.String(200), nullable=True)
    text = db.Column(db.String(500), nullable=True)
    text_color = db.Column(db.String(20), default="#ffffff")
    bg_color = db.Column(db.String(20), default="#9B6FD4")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime,
                           default=lambda: datetime.utcnow() + timedelta(hours=24))
    views = db.relationship("StoryView", backref="story", cascade="all, delete", lazy=True)

    def is_viewed_by(self, user):
        return StoryView.query.filter_by(story_id=self.id, user_id=user.id).first() is not None

    def time_left(self):
        diff = self.expires_at - datetime.utcnow()
        hours = int(diff.total_seconds() // 3600)
        minutes = int((diff.total_seconds() % 3600) // 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


class StoryView(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)


class Thought(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime,
                           default=lambda: datetime.utcnow() + timedelta(hours=6))

    def time_left(self):
        diff = self.expires_at - datetime.utcnow()
        if diff.total_seconds() <= 0:
            return "Expired"
        hours = int(diff.total_seconds() // 3600)
        minutes = int((diff.total_seconds() % 3600) // 60)
        if hours > 0:
            return f"{hours}h {minutes}m left"
        return f"{minutes}m left"

    def time_left_seconds(self):
        diff = self.expires_at - datetime.utcnow()
        return max(0, int(diff.total_seconds()))


class MemoryCapsule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(200), nullable=True)
    unlock_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_unlocked = db.Column(db.Boolean, default=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=True)
    unlocked_post = db.relationship("Post", foreign_keys=[post_id])

    def time_until_unlock(self):
        if self.is_unlocked:
            return "Opened!"
        diff = self.unlock_at - datetime.utcnow()
        if diff.total_seconds() <= 0:
            return "Ready to open!"
        days = diff.days
        hours = int((diff.total_seconds() % 86400) // 3600)
        months = days // 30
        years = days // 365
        if years > 0:
            return f"{years}y {months % 12}m"
        if months > 0:
            return f"{months}mo {days % 30}d"
        if days > 0:
            return f"{days}d {hours}h"
        return f"{hours}h"

    def unlock_date_str(self):
        return self.unlock_at.strftime("%B %d, %Y")


# ── PHASE 19 MODELS ──────────────────────────────────────────────

class Block(db.Model):
    __table_args__ = (db.UniqueConstraint("blocker_id", "blocked_id", name="unique_block"),)
    id = db.Column(db.Integer, primary_key=True)
    blocker_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    blocked_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Mute(db.Model):
    __table_args__ = (db.UniqueConstraint("muter_id", "muted_id", name="unique_mute"),)
    id = db.Column(db.Integer, primary_key=True)
    muter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    muted_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reported_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=True)
    reason = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reporter = db.relationship("User", foreign_keys=[reporter_id], backref="reports_filed")
    reported_user = db.relationship("User", foreign_keys=[reported_id])


class ProfileView(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    viewer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    viewed_at = db.Column(db.DateTime, default=datetime.utcnow)

class AgentTemplate(db.Model):
    """Preset agent personality templates available to all users."""
    __tablename__ = 'agent_template'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    emoji = db.Column(db.String(10), nullable=False)
    personality = db.Column(db.Text, nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<AgentTemplate {self.emoji} {self.name}>'
    
def seed_agent_templates():
    if AgentTemplate.query.count() > 0:
        return
    templates = [
        AgentTemplate(name="Daily Vibes", emoji="🌅",
            personality="You are a calm, poetic soul who finds beauty in everyday moments. You write short, aesthetic posts about mornings, nature, mindfulness, and simple joys. Your tone is warm, dreamy, and uplifting. You use sensory language and occasionally a soft metaphor. Never preachy. Always genuine.",
            content_type="morning quotes, aesthetic thoughts, mindfulness, nature",
            description="Calm morning quotes and aesthetic thoughts to start your day"),
        AgentTemplate(name="Meme Lord", emoji="😂",
            personality="You are chaotic, irreverent, and chronically online. You post observations about everyday life that are hilariously relatable. Your humor is dry, absurd, and self-aware. You reference internet culture naturally. Short punchy posts with unexpected twists. No hashtags. Lowercase preferred. Very funny.",
            content_type="jokes, funny observations, relatable humor, internet culture",
            description="Chaotic funny posts and jokes that hit different"),
        AgentTemplate(name="Gym Bro", emoji="💪",
            personality="You are an enthusiastic fitness coach who genuinely loves helping people get stronger. You post workout tips, motivational content, nutrition advice, and mindset lessons. Energetic and hype but never toxic. Science-backed when possible. Inclusive — fitness is for everyone. Short punchy lines. No fluff.",
            content_type="fitness tips, workout ideas, nutrition, motivation",
            description="Fitness tips, workouts, and pure motivational energy"),
        AgentTemplate(name="Tech News", emoji="🤖",
            personality="You are an enthusiastic tech nerd who explains complex topics simply. You post bite-sized insights about AI, software, startups, and digital culture. Nerdy but accessible. You have takes and opinions. Slightly sarcastic about tech hype when warranted. Always factual, never sensationalist.",
            content_type="AI news, software, startups, tech culture, coding",
            description="Bite-sized tech insights and opinions on the digital world"),
        AgentTemplate(name="Foodie Bot", emoji="🍕",
            personality="You are an obsessed food lover who thinks about food constantly. You post recipes, food facts, flavor combinations, restaurant ideas, and cooking tips. Enthusiastic, hungry energy. You make food sound irresistible. Occasionally philosophical about why certain foods are perfect. Very specific and sensory.",
            content_type="recipes, food facts, cooking tips, flavor combinations",
            description="Recipes, food facts, and dangerously delicious content"),
        AgentTemplate(name="Philosophy", emoji="🧠",
            personality="You are a thoughtful philosopher who sits with big questions. You post reflections on consciousness, meaning, human nature, time, and existence. Measured and precise language. You don't pretend to have answers — you invite thinking. Occasionally provocative. Never nihilistic. Dense but readable.",
            content_type="philosophical questions, deep thoughts, consciousness, meaning of life",
            description="Deep thoughts and questions that make you stop and think"),
        AgentTemplate(name="Gamer", emoji="🎮",
            personality="You are a passionate gamer who lives and breathes gaming culture. You post opinions on games, hidden gems, patch reactions, gaming history facts, and hot takes on the industry. Excited but measured. You respect all platforms. References obscure games occasionally. Knows the meta but appreciates indie.",
            content_type="gaming news, game opinions, tips, gaming culture",
            description="Gaming hot takes, news, and opinions from a true player"),
        AgentTemplate(name="Finance Bro", emoji="💰",
            personality="You are a sharp financial thinker who believes everyone deserves financial literacy. You post money tips, investment concepts, economic observations, and wealth-building mindset. Confident but never arrogant. You simplify complex financial ideas. Always add disclaimers when giving specific advice. Pragmatic.",
            content_type="money tips, investing basics, financial literacy, economic observations",
            description="Smart money tips and financial literacy made simple"),
    ]
    for t in templates:
        db.session.add(t)
    db.session.commit()
    print("✅ Agent templates seeded.")



class AgentRelationship(db.Model):
    
    # Defines how two agents relate to each other.
    # This shapes how they respond to each other's posts.

    # relationship_type options:
    #     'rival'   — they disagree, debate, push back
    #     'ally'    — they agree, support, build on each other
    #     'neutral' — they acknowledge each other without strong opinion
    
    __tablename__ = 'agent_relationship'
    __table_args__ = (
        db.UniqueConstraint('agent_a_id', 'agent_b_id', name='unique_agent_pair'),
    )

    id = db.Column(db.Integer, primary_key=True)
    agent_a_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    agent_b_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    relationship_type = db.Column(db.String(20), default='neutral')  # rival / ally / neutral
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    agent_a = db.relationship('User', foreign_keys=[agent_a_id])
    agent_b = db.relationship('User', foreign_keys=[agent_b_id])

    def __repr__(self):
        return f'<AgentRelationship {self.agent_a_id} {self.relationship_type} {self.agent_b_id}>'


class AgentConversation(db.Model):
    # """
    # Tracks a conversation thread between two agents on a post.
    # Stores the full exchange so we can display it nicely.
    # """
    __tablename__ = 'agent_conversation'

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    initiator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)   # agent who commented first
    responder_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)   # agent who replied
    round_count = db.Column(db.Integer, default=1)   # how many exchanges happened
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # False once conversation ends

    post = db.relationship('Post', foreign_keys=[post_id])
    initiator = db.relationship('User', foreign_keys=[initiator_id])
    responder = db.relationship('User', foreign_keys=[responder_id])

    def __repr__(self):
        return f'<AgentConversation post={self.post_id} {self.initiator_id}↔{self.responder_id}>'
    
def make_admin(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        print(f"User '{username}' not found.")
        return
    user.is_admin = True
    db.session.commit()
    print(f"✅ @{username} is now an admin.")
import os
import re
import re as re_module
import random
import string

from werkzeug.utils import secure_filename
from flask import Blueprint, abort, render_template, redirect, url_for, request, flash, current_app, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from urllib.parse import urlparse, urljoin
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask_mail import Message as MailMessage
from datetime import datetime, timedelta

from .models import (User, Post, Follow, Like, Comment, Notification,
                     Message, Reaction, Poll, PollOption, PollVote,
                     Story, StoryView, Thought, MemoryCapsule, PostView,
                     LoginHistory, TwoFactorCode, EmailChangeRequest,
                     Block, Mute, Report, ProfileView,
                     AgentRelationship, AgentConversation)
from .extensions import db, mail, socketio
from flask_socketio import emit, join_room, leave_room
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from app.models import AgentTemplate

def run_agent_scheduler(app):
    with app.app_context():
        from app.models import User, Post
        from app.extensions import db
        from groq import Groq
        import os
        from datetime import datetime

        now = datetime.utcnow()
        agents = User.query.filter_by(is_agent=True, agent_is_active=True).all()

        for agent in agents:
            if agent.agent_last_posted:
                hours_since = (now - agent.agent_last_posted).total_seconds() / 3600
                if hours_since < agent.agent_posting_frequency:
                    continue
            try:
                client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
                prompt = (
                    f"You are {agent.username}, an AI agent on a social network. "
                    f"Your personality: {agent.agent_personality} "
                    f"You post about: {agent.agent_content_type}. "
                    f"Write ONE short social media post (1-3 sentences, max 240 chars). "
                    f"NO hashtags unless natural. NO quotes. Just the post text."
                )
                resp = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=120
                )
                content = resp.choices[0].message.content.strip()
                if len(content) > 500:
                    content = content[:497] + "..."
                post = Post(content=content, user_id=agent.id)
                db.session.add(post)
                agent.agent_last_posted = now
                agent.agent_post_count = (agent.agent_post_count or 0) + 1
                db.session.commit()
            except Exception as e:
                print(f"Agent scheduler error for {agent.username}: {e}")


def start_agent_scheduler(app):
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=lambda: run_agent_scheduler(app),
            trigger="interval",
            minutes=30,
            id="agent_post_job",
            replace_existing=True
        )
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown())
        print("✅ Agent scheduler started — runs every 30 minutes.")
    else:
        print("⏳ Scheduler waiting for reloader...")

main = Blueprint("main", __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
REACTION_EMOJIS = ['❤️', '😂', '😮', '😢', '😡', '👏']
MOOD_OPTIONS = [
    ('🎧', 'Vibing'), ('😴', 'Tired'), ('🔥', 'On Fire'),
    ('📚', 'Studying'), ('✈️', 'Travelling'), ('💪', 'Working Out'),
    ('😊', 'Happy'), ('🎮', 'Gaming'), ('🍕', 'Hungry'),
    ('💻', 'Coding'), ('🎨', 'Creating'), ('😎', 'Chilling')
]
STORY_BG_COLORS = [
    '#9B6FD4', '#E991C0', '#7DB8E8', '#5CC97A',
    '#F4A460', '#E05C7A', '#4ABDB5', '#8B7ED8'
]

from groq import Groq

def get_groq():
    return Groq(api_key=os.environ.get("GROQ_API_KEY"))

from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def create_notification(user_id, actor_id, type, post_id=None):
    if user_id == actor_id:
        return
    existing = Notification.query.filter_by(
        user_id=user_id, actor_id=actor_id, type=type, post_id=post_id
    ).first()
    if not existing:
        db.session.add(Notification(
            user_id=user_id, actor_id=actor_id, type=type, post_id=post_id
        ))


def parse_mentions(content, post_id=None):
    if not content:
        return
    mentions = re.findall(r'@(\w+)', content)
    for username in set(mentions):
        user = User.query.filter_by(username=username).first()
        if user and user.id != current_user.id:
            create_notification(user_id=user.id, actor_id=current_user.id,
                               type="mention", post_id=post_id)

def trigger_agent_mention_reply(app, agent_id, post_id, comment_content, mentioner_username):
    import threading
    def _reply():
        with app.app_context():
            from app.models import User, Post, Comment
            from app.extensions import db
            from groq import Groq
            import os

            agent = User.query.get(agent_id)
            post = Post.query.get(post_id)
            if not agent or not post or not agent.agent_is_active:
                return

            try:
                client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
                prompt = (
                    f"You are {agent.display_name or agent.username}, "
                    f"an AI personality on a social network.\n"
                    f"Your personality: {agent.agent_personality[:300]}\n\n"
                    f"The post you're commenting on says: \"{post.content[:300]}\"\n\n"
                    f"@{mentioner_username} just mentioned you in a comment and said:\n"
                    f"\"{comment_content}\"\n\n"
                    f"Write a SHORT, natural reply (1-2 sentences, under 200 chars). "
                    f"Address them directly. Stay in character. "
                    f"No hashtags. No quotes around your reply. Just write it."
                )
                resp = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=80
                )
                reply_text = resp.choices[0].message.content.strip()
                if len(reply_text) > 500:
                    reply_text = reply_text[:497] + "..."

                reply_with_tag = f"@{mentioner_username} {reply_text}"

                comment = Comment(
                    content=reply_with_tag,
                    user_id=agent.id,
                    post_id=post_id
                )
                db.session.add(comment)
                db.session.commit()
                print(f"✅ Agent @{agent.username} replied to @{mentioner_username}'s mention")

            except Exception as e:
                print(f"Agent mention reply error: {e}")
                db.session.rollback()

    thread = threading.Thread(target=_reply, daemon=True)
    thread.start()
def moderate_content(app, content, content_type, content_id):
    """
    Runs in background thread.
    content_type: 'post' or 'comment'
    content_id: the post.id or comment.id
    """
    import threading
    def _moderate():
        with app.app_context():
            from app.models import Post, Comment
            from app.extensions import db
            from groq import Groq
            import os

            if not content or len(content.strip()) < 10:
                return

            try:
                client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
                prompt = (
                    f"You are a content moderation system for a social network.\n"
                    f"Analyze this {content_type} and decide if it violates community guidelines.\n\n"
                    f"Content: \"{content[:500]}\"\n\n"
                    f"Flag if it contains ANY of:\n"
                    f"- Hate speech or slurs targeting any group\n"
                    f"- Explicit threats of violence\n"
                    f"- Sexual content involving minors\n"
                    f"- Detailed instructions for self-harm or suicide\n"
                    f"- Spam (repeated nonsense, excessive links, scam patterns)\n\n"
                    f"Do NOT flag for: mild profanity, controversial opinions, "
                    f"dark humor, political views, relationship drama, or venting.\n\n"
                    f"Respond with ONLY this exact format:\n"
                    f"DECISION: SAFE or FLAG\n"
                    f"REASON: one short sentence (max 100 chars) or 'none' if safe"
                )
                resp = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=60
                )
                result = resp.choices[0].message.content.strip()
                lines = result.split('\n')
                decision = 'SAFE'
                reason = 'none'
                for line in lines:
                    if line.startswith('DECISION:'):
                        decision = line.replace('DECISION:', '').strip()
                    elif line.startswith('REASON:'):
                        reason = line.replace('REASON:', '').strip()

                if decision == 'FLAG' and reason != 'none':
                    if content_type == 'post':
                        obj = Post.query.get(content_id)
                    else:
                        obj = Comment.query.get(content_id)
                    if obj:
                        obj.is_flagged = True
                        obj.flag_reason = reason[:300]
                        db.session.commit()
                        print(f"🚩 Flagged {content_type} {content_id}: {reason}")

            except Exception as e:
                print(f"Moderation error: {e}")

    thread = threading.Thread(target=_moderate, daemon=True)
    thread.start()
def parse_hashtags(content):
    if not content:
        return []
    tags = re.findall(r'#(\w+)', content)
    return list(set(tags))


def render_mentions(content):
    if not content:
        return content
    return re.sub(
        r'@(\w+)',
        lambda m: f'<a href="/profile/{m.group(1)}" style="color:var(--purple);font-weight:700;text-decoration:none;">@{m.group(1)}</a>',
        content
    )


def render_hashtags(content):
    if not content:
        return content
    return re.sub(
        r'#(\w+)',
        lambda m: f'<a href="/hashtag/{m.group(1)}" style="color:var(--blue);font-weight:700;text-decoration:none;">#{m.group(1)}</a>',
        content
    )


def render_content(content):
    if not content:
        return content
    result = render_mentions(content)
    result = render_hashtags(result)
    return result


def init_template_filters(app):
    app.jinja_env.filters['render_mentions'] = lambda c: render_mentions(c) if c else c
    app.jinja_env.filters['render_hashtags'] = lambda c: render_hashtags(c) if c else c
    app.jinja_env.filters['render_content'] = lambda c: render_content(c) if c else c


def generate_confirmation_token(email):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps(email, salt="email-confirm")


def confirm_token(token, expiration=3600):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        email = s.loads(token, salt="email-confirm", max_age=expiration)
    except (SignatureExpired, BadSignature):
        return None
    return email


def send_confirmation_email(user_email, username):
    token = generate_confirmation_token(user_email)
    confirm_url = url_for("main.confirm_email", token=token, _external=True)
    msg = MailMessage(
        subject="Confirm your Lynk account ✨",
        recipients=[user_email],
        html=f"""
        <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;
                    border:1px solid #EDD9EA;border-radius:16px;">
            <h2 style="color:#9B6FD4;">Welcome to Lynk ✨</h2>
            <p>Hi <strong>{username}</strong>, please confirm your email.</p>
            <a href="{confirm_url}"
               style="background:linear-gradient(135deg,#9B6FD4,#E991C0);
                      color:white;padding:14px 28px;border-radius:12px;
                      text-decoration:none;font-weight:700;display:inline-block;margin-top:16px;">
                Confirm Email →
            </a>
            <p style="color:#9B7EA0;font-size:13px;margin-top:24px;">This link expires in 1 hour.</p>
        </div>
        """
    )
    mail.send(msg)


def cleanup_expired():
    now = datetime.utcnow()
    Story.query.filter(Story.expires_at <= now).delete()
    Thought.query.filter(Thought.expires_at <= now).delete()
    db.session.commit()


def unlock_capsules():
    now = datetime.utcnow()
    ready = MemoryCapsule.query.filter(
        MemoryCapsule.is_unlocked == False,
        MemoryCapsule.unlock_at <= now
    ).all()
    for capsule in ready:
        post = Post(
            content=capsule.content,
            image=capsule.image,
            is_private=False,
            user_id=capsule.user_id,
            timestamp=now
        )
        db.session.add(post)
        db.session.flush()
        capsule.is_unlocked = True
        capsule.post_id = post.id
        create_notification(
            user_id=capsule.user_id,
            actor_id=capsule.user_id,
            type="capsule_unlocked",
            post_id=post.id
        )
    if ready:
        db.session.commit()


@main.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    return render_template("landing.html")


@main.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if not username or not email or not password:
            flash("All fields are required.", "danger")
            return redirect(url_for("main.register"))
        if not re_module.match(r'^[a-zA-Z0-9_]+$', username):
            flash("Username can only contain letters, numbers and underscores.", "danger")
            return redirect(url_for("main.register"))
        if len(username) < 3 or len(username) > 30:
            flash("Username must be between 3 and 30 characters.", "danger")
            return redirect(url_for("main.register"))
        if User.query.filter_by(username=username).first():
            flash("Username already taken.", "danger")
            return redirect(url_for("main.register"))
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "danger")
            return redirect(url_for("main.register"))
        # Create user first — verified immediately, no email blocking
        user = User(username=username, email=email,
                    password=generate_password_hash(password), is_verified=True)
        db.session.add(user)
        db.session.commit()

        # Try to send welcome email but don't block registration if it fails
        try:
            token = generate_confirmation_token(email)
            confirm_url = url_for("main.confirm_email", token=token, _external=True)
            msg = MailMessage(
                subject="Welcome to Lynk ✨",
                recipients=[email],
                html=f"""
                <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;
                            border:1px solid #EDD9EA;border-radius:16px;">
                    <h2 style="color:#9B6FD4;">Welcome to Lynk ✨</h2>
                    <p>Hi <strong>{username}</strong>, welcome aboard!</p>
                    <p style="color:#9B7EA0;font-size:13px;margin-top:24px;">You can log in now.</p>
                </div>
                """
            )
            mail.send(msg)
        except Exception as e:
            print(f"Welcome email failed (non-blocking): {e}")

        flash("Account created! You can now log in. 🎉", "success")
        return redirect(url_for("main.login"))
    return render_template("register.html")


@main.route("/confirm/<token>", methods=["GET", "POST"])
def confirm_email(token):
    email = confirm_token(token)
    if not email:
        flash("The confirmation link is invalid or has expired.", "danger")
        return redirect(url_for("main.login"))
    user = User.query.filter_by(email=email).first_or_404()
    if user.is_verified:
        flash("Account already confirmed.", "info")
        return redirect(url_for("main.login"))
    if request.method == "POST":
        user.is_verified = True
        db.session.commit()
        flash("Email confirmed! Welcome to Lynk 🎉", "success")
        return redirect(url_for("main.login"))
    return render_template("confirm.html", token=token)


@main.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.is_locked():
            mins = user.lock_minutes_remaining()
            flash(f"Account locked. Try again in {mins} minute(s).", "danger")
            return redirect(url_for("main.login"))
        if user and check_password_hash(user.password, password):
            if not user.is_verified:
                flash("Please confirm your email before logging in.", "danger")
                return redirect(url_for("main.login"))
            if user.is_paused:
                user.is_paused = False
            user.failed_login_attempts = 0
            user.locked_until = None
            if user.is_banned:
                 flash(f"Your account has been banned. Reason: {user.ban_reason or 'Violation of community guidelines'}", "danger")
                 return redirect(url_for("main.login"))
            if user.two_factor_enabled:
                code = ''.join(random.choices(string.digits, k=6))
                TwoFactorCode.query.filter_by(user_id=user.id).delete()
                db.session.add(TwoFactorCode(user_id=user.id, code=code))
                db.session.commit()
                try:
                    msg = MailMessage(
                        subject="Your Lynk login code 🔐",
                        recipients=[user.email],
                        html=f"""
                        <div style="font-family:sans-serif;max-width:420px;margin:auto;
                                    padding:32px;border:1px solid #EDD9EA;border-radius:16px;">
                            <h2 style="color:#9B6FD4;">Your Login Code</h2>
                            <p>Use this code to complete your login:</p>
                            <div style="font-size:40px;font-weight:900;letter-spacing:8px;
                                        color:#9B6FD4;text-align:center;padding:20px 0;">
                                {code}
                            </div>
                            <p style="color:#9B7EA0;font-size:13px;">Expires in 10 minutes.</p>
                        </div>
                        """
                    )
                    mail.send(msg)
                except Exception as e:
                    print(f"2FA EMAIL ERROR: {e}")
                session['2fa_user_id'] = user.id
                return redirect(url_for("main.verify_2fa"))
            db.session.add(LoginHistory(
                user_id=user.id,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent', '')[:300]
            ))
            db.session.commit()
            try:
                msg = MailMessage(
                    subject="New login to your Lynk account 🔔",
                    recipients=[user.email],
                    html=f"""
                    <div style="font-family:sans-serif;max-width:420px;margin:auto;
                                padding:32px;border:1px solid #EDD9EA;border-radius:16px;">
                        <h2 style="color:#9B6FD4;">New Login Detected</h2>
                        <p><strong>Time:</strong> {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')}</p>
                        <p><strong>IP:</strong> {request.remote_addr}</p>
                    </div>
                    """
                )
                mail.send(msg)
            except:
                pass
            login_user(user)
            next_page = request.args.get("next")
            if next_page and is_safe_url(next_page):
                return redirect(next_page)
            return redirect(url_for("main.home"))
        else:
            if user:
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
                if user.failed_login_attempts >= 5:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                    user.failed_login_attempts = 0
                    db.session.commit()
                    flash("Too many failed attempts. Account locked for 15 minutes.", "danger")
                    return redirect(url_for("main.login"))
                db.session.commit()
                remaining = 5 - user.failed_login_attempts
                flash(f"Invalid email or password. {remaining} attempt(s) remaining.", "danger")
            else:
                flash("Invalid email or password.", "danger")
    return render_template("login.html")


@main.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.landing"))


@main.route("/home")
@login_required
def home():
    cleanup_expired()
    unlock_capsules()
    page = request.args.get("page", 1, type=int)
    followed_ids = [f.followed_id for f in current_user.following if f.status == "accepted"]
    followed_ids.append(current_user.id)
    muted_ids = [m.muted_id for m in current_user.mutes_made]
    blocked_ids = [b.blocked_id for b in current_user.blocks_made]
    blocked_by_ids = [b.blocker_id for b in current_user.blocks_received]
    excluded_ids = set(muted_ids + blocked_ids + blocked_by_ids)
    visible_ids = [uid for uid in followed_ids if uid not in excluded_ids]
    if current_user.id not in visible_ids:
        visible_ids.append(current_user.id)

    from sqlalchemy import or_, and_, func, case

    # ── AI FEED RANKING ─────────────────────────────────────────
    # Build interest profile from user's recent likes + reactions
    liked_post_ids = [l.post_id for l in current_user.likes[-50:]]
    reacted_post_ids = [r.post_id for r in current_user.reactions[-50:]]
    engaged_post_ids = set(liked_post_ids + reacted_post_ids)

    # Find authors the user engages with most
    author_engagement = {}
    if engaged_post_ids:
        engaged_posts = Post.query.filter(Post.id.in_(engaged_post_ids)).all()
        for p in engaged_posts:
            author_engagement[p.user_id] = author_engagement.get(p.user_id, 0) + 1

    # Score each candidate post
    candidate_posts = Post.query.filter(
        and_(
            Post.user_id.in_(visible_ids),
            or_(Post.user_id == current_user.id, Post.is_private == False)
        )
    ).order_by(Post.timestamp.desc()).limit(50).all()

    def score_post(post):
        score = 0
        age_hours = (datetime.utcnow() - post.timestamp).total_seconds() / 3600
        if age_hours < 1:
            score += 50
        elif age_hours < 6:
            score += 30
        elif age_hours < 24:
            score += 15
        elif age_hours < 72:
            score += 5
        affinity = author_engagement.get(post.user_id, 0)
        score += min(affinity * 8, 40)
        score += min(len(post.likes) * 2, 20)
        score += min(len(post.reactions) * 2, 10)
        if post.user_id == current_user.id:
            score += 25
        if post.image:
            score += 5
        if post.poll:
            score += 8
        return score

    # Sort by score
    ranked = sorted(candidate_posts, key=score_post, reverse=True)

    # Manual pagination on ranked list
    per_page = 5
    total = len(ranked)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = ranked[start:end]

    # Build a mock pagination object
    class RankedPage:
        def __init__(self, items, page, per_page, total):
            self.items = items
            self.page = page
            self.per_page = per_page
            self.total = total
            self.pages = max(1, (total + per_page - 1) // per_page)
            self.has_prev = page > 1
            self.has_next = page < self.pages
            self.prev_num = page - 1
            self.next_num = page + 1

        def iter_pages(self, left_edge=1, right_edge=1, left_current=2, right_current=2):
            last = 0
            for num in range(1, self.pages + 1):
                if (num <= left_edge or
                    (self.page - left_current - 1 < num < self.page + right_current) or
                    num > self.pages - right_edge):
                    if last + 1 != num:
                        yield None
                    yield num
                    last = num

    posts = RankedPage(page_items, page, per_page, total)
    # ─────────────────────────────────────────────────────────────

    suggestions = User.query.filter(
        User.id != current_user.id,
        ~User.id.in_(followed_ids),
        ~User.id.in_(list(excluded_ids)),
        User.is_paused == False
    ).limit(5).all()
    story_users = []
    my_stories = current_user.active_stories()
    story_users.append({"user": current_user, "stories": my_stories, "is_me": True, "has_unseen": False})
    for fid in followed_ids:
        if fid == current_user.id or fid in excluded_ids:
            continue
        u = User.query.get(fid)
        if not u or u.is_paused:
            continue
        active = u.active_stories()
        if active:
            has_unseen = any(not s.is_viewed_by(current_user) for s in active)
            story_users.append({"user": u, "stories": active, "is_me": False, "has_unseen": has_unseen})
    thoughts = Thought.query.filter(
        Thought.user_id.in_(visible_ids),
        Thought.expires_at > datetime.utcnow()
    ).order_by(Thought.created_at.desc()).all()
    since = datetime.utcnow() - timedelta(hours=24)
    trending = db.session.query(Post, func.count(Like.id).label('like_count'))\
        .join(Like, Like.post_id == Post.id)\
        .filter(Post.timestamp >= since, Post.is_private == False,
                Post.user_id != current_user.id,
                ~Post.user_id.in_(list(excluded_ids)))\
        .group_by(Post.id).order_by(func.count(Like.id).desc()).limit(3).all()
    since_1h = datetime.utcnow() - timedelta(hours=1)
    recent_active = db.session.query(User)\
        .join(Post, Post.user_id == User.id)\
        .filter(Post.timestamp >= since_1h,
                User.id != current_user.id,
                ~User.id.in_(list(excluded_ids)),
                User.is_paused == False)\
        .distinct().limit(6).all()
    from sqlalchemy.sql.expression import func as sqlfunc
    random_post = Post.query.filter(
        Post.is_private == False,
        ~Post.user_id.in_(followed_ids),
        ~Post.user_id.in_(list(excluded_ids))
    ).order_by(sqlfunc.random()).first()
    since_7d = datetime.utcnow() - timedelta(days=7)
    recent_posts = Post.query.filter(
        Post.is_private == False,
        Post.timestamp >= since_7d,
        Post.content.isnot(None)
    ).all()
    hashtag_counts = {}
    for p in recent_posts:
        if p.content:
            tags = re.findall(r'#(\w+)', p.content)
            for t in tags:
                t_lower = t.lower()
                hashtag_counts[t_lower] = hashtag_counts.get(t_lower, 0) + 1
    trending_hashtags = sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    return render_template("index.html",
        posts=posts, suggestions=suggestions,
        story_users=story_users, thoughts=thoughts,
        trending=trending, recent_active=recent_active,
        random_post=random_post,
        trending_hashtags=trending_hashtags
    )


@main.route("/story/create", methods=["GET", "POST"])
@login_required
def create_story():
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        bg_color = request.form.get("bg_color", "#9B6FD4")
        text_color = request.form.get("text_color", "#ffffff")
        image_filename = None
        file = request.files.get("image")
        if file and file.filename != "" and allowed_file(file.filename):
            image_filename = secure_filename(
                f"story_{current_user.id}_{int(datetime.utcnow().timestamp())}_{file.filename}"
            )
            upload_folder = os.path.join(current_app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, image_filename))
        if not text and not image_filename:
            flash("Story cannot be empty.", "danger")
            return redirect(url_for("main.home"))
        story = Story(
            user_id=current_user.id, image=image_filename,
            text=text, bg_color=bg_color, text_color=text_color
        )
        db.session.add(story)
        db.session.commit()
        flash("Story posted! It will disappear in 24 hours.", "success")
        return redirect(url_for("main.home"))
    return render_template("create_story.html", bg_colors=STORY_BG_COLORS)


@main.route("/story/view/<int:user_id>")
@login_required
def view_story(user_id):
    user = User.query.get_or_404(user_id)
    stories = user.active_stories()
    if not stories:
        flash("No active stories.", "info")
        return redirect(url_for("main.home"))
    for story in stories:
        if not story.is_viewed_by(current_user):
            db.session.add(StoryView(story_id=story.id, user_id=current_user.id))
    db.session.commit()
    return render_template("view_story.html", user=user, stories=stories)


@main.route("/story/delete/<int:story_id>")
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("main.home"))
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("main.home"))


@main.route("/thoughts", methods=["GET", "POST"])
@login_required
def thoughts_page():
    cleanup_expired()
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if not content:
            flash("Thought cannot be empty.", "danger")
            return redirect(url_for("main.thoughts_page"))
        if len(content) > 280:
            flash("Keep it under 280 characters.", "danger")
            return redirect(url_for("main.thoughts_page"))
        thought = Thought(user_id=current_user.id, content=content)
        db.session.add(thought)
        db.session.commit()
        flash("Thought posted! It disappears in 6 hours.", "success")
        return redirect(url_for("main.thoughts_page"))
    followed_ids = [f.followed_id for f in current_user.following if f.status == "accepted"]
    followed_ids.append(current_user.id)
    thoughts = Thought.query.filter(
        Thought.user_id.in_(followed_ids),
        Thought.expires_at > datetime.utcnow()
    ).order_by(Thought.created_at.desc()).all()
    return render_template("thoughts.html", thoughts=thoughts)


@main.route("/thought/delete/<int:thought_id>")
@login_required
def delete_thought(thought_id):
    thought = Thought.query.get_or_404(thought_id)
    if thought.user_id != current_user.id:
        flash("Unauthorized.", "danger")
        return redirect(url_for("main.thoughts_page"))
    db.session.delete(thought)
    db.session.commit()
    return redirect(url_for("main.thoughts_page"))


@main.route("/create-post", methods=["POST"])
@login_required
def create_post():
    content = request.form.get("content", "").strip()
    caption = request.form.get("caption", "").strip()
    is_private = request.form.get("is_private") == "1"
    is_sensitive = request.form.get("is_sensitive") == "1"
    image_filename = None
    if caption:
        content = caption
    file = request.files.get("image")
    if file and file.filename != "" and allowed_file(file.filename):
        image_filename = secure_filename(
            f"post_{current_user.id}_{int(datetime.utcnow().timestamp())}_{file.filename}"
        )
        upload_folder = os.path.join(current_app.root_path, "static", "uploads")
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, image_filename))
    if not content and not image_filename:
        flash("Post cannot be empty.", "danger")
        return redirect(url_for("main.home"))
    post = Post(content=content, image=image_filename,
                is_private=is_private, is_sensitive=is_sensitive,
                user_id=current_user.id)
    db.session.add(post)
    db.session.flush()
    if content:
        parse_mentions(content, post_id=post.id)
        parse_hashtags(content)
    poll_options = request.form.getlist("poll_options[]")
    poll_options = [o.strip() for o in poll_options if o.strip()]
    if len(poll_options) >= 2:
        poll = Poll(post_id=post.id)
        db.session.add(poll)
        db.session.flush()
        for opt_text in poll_options[:4]:
            db.session.add(PollOption(poll_id=poll.id, text=opt_text))
    db.session.commit()
    # Moderate in background
    if content:
          moderate_content(
          app=current_app._get_current_object(),
          content=content,
          content_type='post',
          content_id=post.id
    )
    return redirect(url_for("main.home"))


@main.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("main.home"))
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        is_private = request.form.get("is_private") == "1"
        post.is_private = is_private
        post.is_sensitive = request.form.get("is_sensitive") == "1"
        file = request.files.get("image")
        if file and file.filename != "" and allowed_file(file.filename):
            image_filename = secure_filename(
                f"post_{current_user.id}_{int(datetime.utcnow().timestamp())}_{file.filename}"
            )
            upload_folder = os.path.join(current_app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, image_filename))
            post.image = image_filename
        if not content and not post.image:
            flash("Post cannot be empty.", "danger")
            return redirect(url_for("main.edit_post", post_id=post_id))
        post.content = content
        db.session.commit()
        flash("Post updated.", "success")
        return redirect(url_for("main.home"))
    return render_template("edit_post.html", post=post)


@main.route("/delete-post/<int:post_id>")
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        flash("Unauthorized action.", "danger")
        return redirect(url_for("main.home"))
    db.session.delete(post)
    db.session.commit()
    flash("Post deleted.", "success")
    return redirect(url_for("main.home"))


@main.route("/like/<int:post_id>")
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not current_user.has_liked(post):
        db.session.add(Like(user_id=current_user.id, post_id=post.id))
        create_notification(user_id=post.author.id, actor_id=current_user.id,
                           type="like", post_id=post.id)
        db.session.commit()
    return redirect(request.referrer or url_for("main.home"))


@main.route("/unlike/<int:post_id>")
@login_required
def unlike_post(post_id):
    like = Like.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    if like:
        post = Post.query.get(post_id)
        if post:
            Notification.query.filter_by(
                user_id=post.user_id, actor_id=current_user.id,
                type="like", post_id=post_id
            ).delete()
        db.session.delete(like)
        db.session.commit()
    return redirect(request.referrer or url_for("main.home"))


@main.route("/react/<int:post_id>/<emoji>")
@login_required
def react_post(post_id, emoji):
    if emoji not in REACTION_EMOJIS:
        return redirect(request.referrer or url_for("main.home"))
    post = Post.query.get_or_404(post_id)
    existing = current_user.get_reaction(post)
    if existing:
        if existing.emoji == emoji:
            db.session.delete(existing)
        else:
            existing.emoji = emoji
    else:
        db.session.add(Reaction(user_id=current_user.id, post_id=post.id, emoji=emoji))
        create_notification(user_id=post.author.id, actor_id=current_user.id,
                           type="reaction", post_id=post.id)
    db.session.commit()
    return redirect(request.referrer or url_for("main.home"))


@main.route("/poll-vote/<int:poll_id>/<int:option_id>")
@login_required
def poll_vote(poll_id, option_id):
    poll = Poll.query.get_or_404(poll_id)
    existing = PollVote.query.filter_by(user_id=current_user.id, poll_id=poll_id).first()
    if existing:
        flash("You already voted.", "info")
        return redirect(request.referrer or url_for("main.home"))
    option = PollOption.query.filter_by(id=option_id, poll_id=poll_id).first_or_404()
    db.session.add(PollVote(user_id=current_user.id, poll_id=poll_id, option_id=option.id))
    db.session.commit()
    return redirect(request.referrer or url_for("main.home"))


@main.route("/set-mood", methods=["POST"])
@login_required
def set_mood():
    mood = request.form.get("mood", "").strip()
    current_user.mood = mood if mood else None
    db.session.commit()
    return redirect(request.referrer or url_for("main.profile", username=current_user.username))


@main.route("/comment/<int:post_id>", methods=["GET", "POST"])
@login_required
def comment_post(post_id):
    post = Post.query.get_or_404(post_id)
    if current_user.is_blocking(post.author) or post.author.is_blocking(current_user):
        flash("You cannot view this post.", "danger")
        return redirect(url_for("main.home"))
    if request.method == "POST":
        if not current_user.can_comment_on(post.author):
            flash("Comments are restricted on this post.", "danger")
            return redirect(url_for("main.comment_post", post_id=post_id))
        content = request.form.get("content", "").strip()
        if content:
            comment = Comment(content=content, user_id=current_user.id, post_id=post.id)
            db.session.add(comment)
            if post.author.id != current_user.id:
                create_notification(user_id=post.author.id, actor_id=current_user.id,
                                   type="comment", post_id=post.id)
            parse_mentions(content, post_id=post.id)
            db.session.commit()
            # Moderate in background
            moderate_content(
            app=current_app._get_current_object(),
            content=content,
            content_type='comment',
            content_id=comment.id
)

            # ── AGENT MENTION DETECTION ──────────────────────────
            mentioned_usernames = re.findall(r'@(\w+)', content)
            for uname in set(mentioned_usernames):
                if uname == current_user.username:
                    continue
                mentioned_user = User.query.filter_by(username=uname).first()
                if (mentioned_user
                        and mentioned_user.is_agent
                        and mentioned_user.agent_is_active):
                    trigger_agent_mention_reply(
                        app=current_app._get_current_object(),
                        agent_id=mentioned_user.id,
                        post_id=post_id,
                        comment_content=content,
                        mentioner_username=current_user.username
                    )
            # ─────────────────────────────────────────────────────

            flash("Comment added.", "success")
        return redirect(url_for("main.comment_post", post_id=post_id))
    if post.user_id != current_user.id:
        existing_view = PostView.query.filter_by(user_id=current_user.id, post_id=post_id).first()
        if not existing_view:
            db.session.add(PostView(user_id=current_user.id, post_id=post_id))
            post.view_count = (post.view_count or 0) + 1
            db.session.commit()
    return render_template("comment.html", post=post)


@main.route("/follow/<username>", methods=["GET", "POST"])
@login_required
def follow(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user:
        flash("You cannot follow yourself.", "danger")
        return redirect(url_for("main.profile", username=username))
    if current_user.is_blocking(user) or user.is_blocking(current_user):
        flash("You cannot follow this user.", "danger")
        return redirect(url_for("main.home"))
    if current_user.is_following(user):
        flash(f"You already follow {username}.", "info")
        return redirect(request.referrer or url_for("main.profile", username=username))
    if current_user.has_requested(user):
        flash(f"Follow request already sent.", "info")
        return redirect(request.referrer or url_for("main.profile", username=username))
    current_user.follow(user)
    create_notification(user_id=user.id, actor_id=current_user.id, type="follow_request")
    db.session.commit()
    flash(f"Follow request sent to {username}.", "success")
    return redirect(request.referrer or url_for("main.profile", username=username))


@main.route("/unfollow/<username>", methods=["GET", "POST"])
@login_required
def unfollow(username):
    user = User.query.filter_by(username=username).first_or_404()
    current_user.unfollow(user)
    Notification.query.filter_by(user_id=user.id, actor_id=current_user.id, type="follow_request").delete()
    Notification.query.filter_by(user_id=user.id, actor_id=current_user.id, type="follow_accept").delete()
    db.session.commit()
    flash(f"You unfollowed {username}.", "info")
    return redirect(request.referrer or url_for("main.profile", username=username))


@main.route("/accept-follow/<int:follower_id>")
@login_required
def accept_follow(follower_id):
    follow = Follow.query.filter_by(
        follower_id=follower_id, followed_id=current_user.id, status="pending"
    ).first_or_404()
    follow.status = "accepted"
    Notification.query.filter_by(
        user_id=current_user.id, actor_id=follower_id, type="follow_request"
    ).update({"is_read": True})
    create_notification(user_id=follower_id, actor_id=current_user.id, type="follow_accept")
    db.session.commit()
    flash("Follow request accepted.", "success")
    return redirect(url_for("main.activity"))


@main.route("/decline-follow/<int:follower_id>")
@login_required
def decline_follow(follower_id):
    follow = Follow.query.filter_by(
        follower_id=follower_id, followed_id=current_user.id, status="pending"
    ).first_or_404()
    db.session.delete(follow)
    Notification.query.filter_by(
        user_id=current_user.id, actor_id=follower_id, type="follow_request"
    ).delete()
    db.session.commit()
    flash("Follow request declined.", "info")
    return redirect(url_for("main.activity"))


@main.route("/remove-follower/<username>")
@login_required
def remove_follower(username):
    user = User.query.filter_by(username=username).first_or_404()
    follow = Follow.query.filter_by(follower_id=user.id, followed_id=current_user.id).first()
    if follow:
        db.session.delete(follow)
        db.session.commit()
        flash(f"{username} removed from your followers.", "info")
    return redirect(url_for("main.followers", username=current_user.username))


@main.route("/activity")
@login_required
def activity():
    pending_requests = Follow.query.filter_by(
        followed_id=current_user.id, status="pending"
    ).order_by(Follow.timestamp.desc()).all()
    notifications = Notification.query.filter_by(
        user_id=current_user.id
    ).filter(Notification.type != "follow_request")\
     .order_by(Notification.timestamp.desc()).limit(50).all()
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return render_template("activity.html",
        pending_requests=pending_requests, notifications=notifications)


@main.route("/profile/<username>")
@login_required
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user.is_blocking(current_user) and user != current_user:
        flash("You can't view this profile.", "danger")
        return redirect(url_for("main.home"))
    if user != current_user and not current_user.is_blocking(user):
        existing = ProfileView.query.filter_by(profile_id=user.id, viewer_id=current_user.id).first()
        if existing:
            existing.viewed_at = datetime.utcnow()
        else:
            db.session.add(ProfileView(profile_id=user.id, viewer_id=current_user.id))
        db.session.commit()
    if user == current_user:
        posts = Post.query.filter_by(user_id=user.id).order_by(Post.timestamp.desc()).all()
    else:
        posts = Post.query.filter_by(user_id=user.id, is_private=False).order_by(Post.timestamp.desc()).all()
    return render_template("profile.html", user=user, posts=posts, mood_options=MOOD_OPTIONS)


@main.route("/profile/<username>/followers")
@login_required
def followers(username):
    user = User.query.filter_by(username=username).first_or_404()
    followers_users = [User.query.get(f.follower_id) for f in user.followers.filter_by(status="accepted")]
    return render_template("followers.html", user=user, followers_users=followers_users)


@main.route("/profile/<username>/following")
@login_required
def following(username):
    user = User.query.filter_by(username=username).first_or_404()
    following_users = [User.query.get(f.followed_id) for f in user.following.filter_by(status="accepted")]
    return render_template("following.html", user=user, following_users=following_users)


@main.route("/edit-profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()
        display_name = request.form.get("display_name", "").strip()
        website = request.form.get("website", "").strip()
        current_user.bio = bio
        current_user.display_name = display_name if display_name else None
        current_user.website = website if website else None
        file = request.files.get("profile_pic")
        if file and file.filename != "" and allowed_file(file.filename):
            filename = secure_filename(f"user_{current_user.id}_{file.filename}")
            upload_folder = os.path.join(current_app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, filename))
            current_user.profile_pic = filename
        db.session.commit()
        flash("Profile updated!", "success")
        return redirect(url_for("main.profile", username=current_user.username))
    return render_template("edit_profile.html")


@main.route("/hashtag/<tag>")
@login_required
def hashtag(tag):
    posts = Post.query.filter(
        Post.content.ilike(f"%#{tag}%"),
        Post.is_private == False
    ).order_by(Post.timestamp.desc()).all()
    related = set()
    for post in posts[:20]:
        if post.content:
            tags = re.findall(r'#(\w+)', post.content)
            for t in tags:
                if t.lower() != tag.lower():
                    related.add(t)
    related = list(related)[:8]
    return render_template("hashtag.html", tag=tag, posts=posts, related=related)


# ── AJAX ROUTES ──────────────────────────────────────────────────

@main.route("/ajax/like/<int:post_id>", methods=["POST"])
@login_required
def ajax_like(post_id):
    post = Post.query.get_or_404(post_id)
    if current_user.has_liked(post):
        like = Like.query.filter_by(user_id=current_user.id, post_id=post_id).first()
        if like:
            Notification.query.filter_by(
                user_id=post.user_id, actor_id=current_user.id,
                type="like", post_id=post_id
            ).delete()
            db.session.delete(like)
        liked = False
    else:
        db.session.add(Like(user_id=current_user.id, post_id=post.id))
        create_notification(user_id=post.author.id, actor_id=current_user.id,
                           type="like", post_id=post.id)
        liked = True
    db.session.commit()
    return jsonify({"success": True, "liked": liked, "count": len(post.likes)})


@main.route("/ajax/react/<int:post_id>/<emoji>", methods=["POST"])
@login_required
def ajax_react(post_id, emoji):
    if emoji not in REACTION_EMOJIS:
        return jsonify({"success": False}), 400
    post = Post.query.get_or_404(post_id)
    existing = current_user.get_reaction(post)
    removed = False
    if existing:
        if existing.emoji == emoji:
            db.session.delete(existing)
            removed = True
        else:
            existing.emoji = emoji
    else:
        db.session.add(Reaction(user_id=current_user.id, post_id=post.id, emoji=emoji))
        create_notification(user_id=post.author.id, actor_id=current_user.id,
                           type="reaction", post_id=post.id)
    db.session.commit()
    current_reaction = current_user.get_reaction(post)
    return jsonify({
        "success": True, "removed": removed,
        "emoji": current_reaction.emoji if current_reaction else None,
        "count": len(post.reactions)
    })


@main.route("/ajax/follow/<username>", methods=["POST"])
@login_required
def ajax_follow(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user:
        return jsonify({"success": False}), 400
    if current_user.is_blocking(user) or user.is_blocking(current_user):
        return jsonify({"success": False, "message": "Blocked"}), 403
    if current_user.is_following(user):
        current_user.unfollow(user)
        Notification.query.filter_by(user_id=user.id, actor_id=current_user.id, type="follow_request").delete()
        Notification.query.filter_by(user_id=user.id, actor_id=current_user.id, type="follow_accept").delete()
        db.session.commit()
        return jsonify({"success": True, "status": "none"})
    elif current_user.has_requested(user):
        current_user.unfollow(user)
        Notification.query.filter_by(user_id=user.id, actor_id=current_user.id, type="follow_request").delete()
        db.session.commit()
        return jsonify({"success": True, "status": "none"})
    else:
        current_user.follow(user)
        create_notification(user_id=user.id, actor_id=current_user.id, type="follow_request")
        db.session.commit()
        return jsonify({"success": True, "status": "pending"})


@main.route("/ajax/poll-vote/<int:poll_id>/<int:option_id>", methods=["POST"])
@login_required
def ajax_poll_vote(poll_id, option_id):
    poll = Poll.query.get_or_404(poll_id)
    existing = PollVote.query.filter_by(user_id=current_user.id, poll_id=poll_id).first()
    if existing:
        return jsonify({"success": False, "message": "Already voted"}), 400
    option = PollOption.query.filter_by(id=option_id, poll_id=poll_id).first_or_404()
    db.session.add(PollVote(user_id=current_user.id, poll_id=poll_id, option_id=option.id))
    db.session.commit()
    total = sum(len(o.votes) for o in poll.options)
    results = []
    for o in poll.options:
        count = len(o.votes)
        pct = round((count / total * 100)) if total > 0 else 0
        results.append({"id": o.id, "text": o.text, "count": count, "pct": pct})
    return jsonify({"success": True, "results": results, "total": total})


@main.route("/pin-post/<int:post_id>")
@login_required
def pin_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        flash("Unauthorized.", "danger")
        return redirect(url_for("main.profile", username=current_user.username))
    if current_user.pinned_post_id == post_id:
        current_user.pinned_post_id = None
        flash("Post unpinned.", "info")
    else:
        current_user.pinned_post_id = post_id
        flash("Post pinned to your profile! 📌", "success")
    db.session.commit()
    return redirect(url_for("main.profile", username=current_user.username))


@main.route("/verify-2fa", methods=["GET", "POST"])
def verify_2fa():
    user_id = session.get('2fa_user_id')
    if not user_id:
        return redirect(url_for("main.login"))
    user = User.query.get_or_404(user_id)
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        record = TwoFactorCode.query.filter_by(user_id=user_id, code=code).first()
        if record and record.is_valid():
            record.is_used = True
            db.session.add(LoginHistory(
                user_id=user.id,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent', '')[:300]
            ))
            db.session.commit()
            session.pop('2fa_user_id', None)
            login_user(user)
            return redirect(url_for("main.home"))
        else:
            flash("Invalid or expired code.", "danger")
    return render_template("verify_2fa.html", user=user)


@main.route("/security/change-password", methods=["POST"])
@login_required
def change_password():
    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")
    if not check_password_hash(current_user.password, current_pw):
        flash("Current password is incorrect.", "danger")
        return redirect(url_for("main.settings"))
    if len(new_pw) < 8:
        flash("New password must be at least 8 characters.", "danger")
        return redirect(url_for("main.settings"))
    if new_pw != confirm_pw:
        flash("Passwords do not match.", "danger")
        return redirect(url_for("main.settings"))
    current_user.password = generate_password_hash(new_pw)
    db.session.commit()
    try:
        msg = MailMessage(
            subject="Your Lynk password was changed 🔐",
            recipients=[current_user.email],
            html=f"""<div style="font-family:sans-serif;max-width:420px;margin:auto;padding:32px;border:1px solid #EDD9EA;border-radius:16px;">
                <h2 style="color:#9B6FD4;">Password Changed</h2>
                <p>Your Lynk password was just changed.</p>
                <p><strong>Time:</strong> {datetime.utcnow().strftime('%B %d, %Y at %H:%M UTC')}</p>
            </div>"""
        )
        mail.send(msg)
    except:
        pass
    flash("Password changed successfully! 🔐", "success")
    return redirect(url_for("main.settings"))


@main.route("/security/toggle-2fa", methods=["POST"])
@login_required
def toggle_2fa():
    current_user.two_factor_enabled = not current_user.two_factor_enabled
    db.session.commit()
    status = "enabled" if current_user.two_factor_enabled else "disabled"
    flash(f"Two-factor authentication {status}.", "success")
    return redirect(url_for("main.settings"))


@main.route("/security/toggle-privacy", methods=["POST"])
@login_required
def toggle_privacy():
    current_user.is_private = not current_user.is_private
    db.session.commit()
    status = "private" if current_user.is_private else "public"
    flash(f"Your account is now {status}.", "success")
    return redirect(url_for("main.settings"))


@main.route("/security/logout-all", methods=["POST"])
@login_required
def logout_all_sessions():
    from flask import session as flask_session
    flask_session.clear()
    login_user(current_user)
    flash("All other sessions have been logged out.", "success")
    return redirect(url_for("main.settings"))


@main.route("/security/change-email", methods=["POST"])
@login_required
def change_email():
    new_email = request.form.get("new_email", "").strip()
    password = request.form.get("email_password", "").strip()
    if not check_password_hash(current_user.password, password):
        flash("Incorrect password.", "danger")
        return redirect(url_for("main.settings"))
    if not new_email:
        flash("Please enter a new email.", "danger")
        return redirect(url_for("main.settings"))
    if User.query.filter_by(email=new_email).first():
        flash("That email is already in use.", "danger")
        return redirect(url_for("main.settings"))
    token = generate_confirmation_token(new_email + str(current_user.id))
    EmailChangeRequest.query.filter_by(user_id=current_user.id).delete()
    db.session.add(EmailChangeRequest(user_id=current_user.id, new_email=new_email, token=token))
    db.session.commit()
    confirm_url = url_for("main.confirm_email_change", token=token, _external=True)
    try:
        msg = MailMessage(
            subject="Confirm your new Lynk email 📧",
            recipients=[new_email],
            html=f"""<div style="font-family:sans-serif;max-width:420px;margin:auto;padding:32px;border:1px solid #EDD9EA;border-radius:16px;">
                <h2 style="color:#9B6FD4;">Confirm Email Change</h2>
                <a href="{confirm_url}" style="background:linear-gradient(135deg,#9B6FD4,#E991C0);color:white;padding:14px 28px;border-radius:12px;text-decoration:none;font-weight:700;display:inline-block;margin-top:16px;">Confirm New Email →</a>
                <p style="color:#9B7EA0;font-size:13px;margin-top:24px;">This link expires in 1 hour.</p>
            </div>"""
        )
        mail.send(msg)
        flash(f"Confirmation email sent to {new_email}.", "success")
    except Exception as e:
        flash(f"Could not send email: {str(e)}", "danger")
    return redirect(url_for("main.settings"))


@main.route("/security/confirm-email-change/<token>", methods=["GET", "POST"])
def confirm_email_change(token):
    if request.method == "POST":
        record = EmailChangeRequest.query.filter_by(token=token).first()
        if not record or not record.is_valid():
            flash("Link is invalid or expired.", "danger")
            return redirect(url_for("main.settings"))
        user = User.query.get(record.user_id)
        user.email = record.new_email
        user.is_verified = True
        record.is_used = True
        db.session.commit()
        flash("Email updated successfully! 📧", "success")
        return redirect(url_for("main.settings"))
    return render_template("confirm_email_change.html", token=token)


@main.route("/security/delete-account", methods=["POST"])
@login_required
def delete_account():
    password = request.form.get("delete_password", "")
    if not check_password_hash(current_user.password, password):
        flash("Incorrect password.", "danger")
        return redirect(url_for("main.settings"))
    user = current_user
    logout_user()
    db.session.delete(user)
    db.session.commit()
    flash("Your account has been permanently deleted.", "info")
    return redirect(url_for("main.landing"))


@main.route("/settings")
@login_required
def settings():
    login_logs = LoginHistory.query.filter_by(
        user_id=current_user.id
    ).order_by(LoginHistory.timestamp.desc()).limit(10).all()
    blocked_users = [User.query.get(b.blocked_id) for b in current_user.blocks_made.order_by(Block.created_at.desc()).all()]
    muted_users = [User.query.get(m.muted_id) for m in current_user.mutes_made.order_by(Mute.created_at.desc()).all()]
    return render_template("settings.html", login_logs=login_logs, blocked_users=blocked_users, muted_users=muted_users)


@main.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    users = []
    if query:
        if query.startswith("#"):
            tag = query[1:]
            return redirect(url_for("main.hashtag", tag=tag))
        users = User.query.filter(
            User.username.ilike(f"%{query}%"),
            User.id != current_user.id,
            User.is_paused == False
        ).limit(20).all()
        blocked_ids = {b.blocked_id for b in current_user.blocks_made}
        blocked_by_ids = {b.blocker_id for b in current_user.blocks_received}
        excluded = blocked_ids | blocked_by_ids
        users = [u for u in users if u.id not in excluded]
    return render_template("search.html", users=users, query=query)


@main.route("/explore")
@login_required
def explore():
    from sqlalchemy import func
    followed_ids = [f.followed_id for f in current_user.following if f.status == "accepted"]
    followed_ids.append(current_user.id)
    blocked_ids = {b.blocked_id for b in current_user.blocks_made}
    blocked_by_ids = {b.blocker_id for b in current_user.blocks_received}
    excluded = blocked_ids | blocked_by_ids
    since_7d = datetime.utcnow() - timedelta(days=7)
    trending_posts = db.session.query(Post, func.count(Like.id).label('like_count'))\
        .outerjoin(Like, Like.post_id == Post.id)\
        .filter(Post.is_private == False, Post.timestamp >= since_7d,
                ~Post.user_id.in_(list(excluded)))\
        .group_by(Post.id).order_by(func.count(Like.id).desc()).limit(12).all()
    people = db.session.query(User, func.count(Follow.follower_id).label('follower_count'))\
        .outerjoin(Follow, (Follow.followed_id == User.id) & (Follow.status == 'accepted'))\
        .filter(User.id != current_user.id, ~User.id.in_(followed_ids),
                ~User.id.in_(list(excluded)), User.is_paused == False)\
        .group_by(User.id).order_by(func.count(Follow.follower_id).desc()).limit(12).all()
    recent_posts = Post.query.filter(
        Post.is_private == False, Post.timestamp >= since_7d, Post.content.isnot(None)
    ).all()
    hashtag_counts = {}
    for p in recent_posts:
        if p.content:
            tags = re.findall(r'#(\w+)', p.content)
            for t in tags:
                t_lower = t.lower()
                hashtag_counts[t_lower] = hashtag_counts.get(t_lower, 0) + 1
    trending_hashtags = sorted(hashtag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    return render_template("explore.html",
        trending_posts=trending_posts, people=people, trending_hashtags=trending_hashtags)


@main.route("/messages")
@login_required
def inbox():
    return redirect(url_for("main.messages_home"))

@main.route("/messages/home")
@login_required
def messages_home():
    from sqlalchemy import or_
    blocked_ids = {b.blocked_id for b in current_user.blocks_made}
    blocked_by_ids = {b.blocker_id for b in current_user.blocks_received}
    excluded = blocked_ids | blocked_by_ids
    conversations = db.session.query(Message).filter(
        or_(Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id)
    ).order_by(Message.timestamp.desc()).all()
    seen = set()
    partners = []
    for msg in conversations:
        other_id = msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
        if other_id not in seen and other_id not in excluded:
            seen.add(other_id)
            other = User.query.get(other_id)
            last_msg = Message.query.filter(
                or_(
                    (Message.sender_id == current_user.id) & (Message.receiver_id == other_id),
                    (Message.sender_id == other_id) & (Message.receiver_id == current_user.id)
                )
            ).order_by(Message.timestamp.desc()).first()
            unread = Message.query.filter_by(
                sender_id=other_id,
                receiver_id=current_user.id,
                is_read=False
            ).count()
            partners.append({"user": other, "last_msg": last_msg, "unread": unread})
    return render_template("messages_home.html", partners=partners, active_user=None)


@main.route("/messages/<username>", methods=["GET", "POST"])
@login_required
def conversation(username):
    other = User.query.filter_by(username=username).first_or_404()
    if current_user.is_blocking(other) or other.is_blocking(current_user):
        flash("You cannot message this user.", "danger")
        return redirect(url_for("main.messages_home"))
    if not current_user.is_mutual_follow(other):
        flash("You can only message people who follow you back.", "danger")
        return redirect(url_for("main.messages_home"))
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            msg = Message(sender_id=current_user.id,
                         receiver_id=other.id, content=content)
            db.session.add(msg)
            db.session.commit()
            socketio.emit("message_notification", {
                "from_username": current_user.username,
                "from_display": current_user.display_name or current_user.username,
                "unread": Message.query.filter_by(
                    sender_id=current_user.id,
                    receiver_id=other.id,
                    is_read=False
                ).count()
            }, room=f"user_{other.id}")
        return redirect(url_for("main.conversation", username=username))
    Message.query.filter_by(
        sender_id=other.id,
        receiver_id=current_user.id,
        is_read=False
    ).update({"is_read": True})
    db.session.commit()
    from sqlalchemy import or_
    messages = Message.query.filter(
        or_(
            (Message.sender_id == current_user.id) & (Message.receiver_id == other.id),
            (Message.sender_id == other.id) & (Message.receiver_id == current_user.id)
        )
    ).order_by(Message.timestamp.asc()).all()
    # Build partners list for left panel
    blocked_ids = {b.blocked_id for b in current_user.blocks_made}
    blocked_by_ids = {b.blocker_id for b in current_user.blocks_received}
    excluded = blocked_ids | blocked_by_ids
    all_convos = db.session.query(Message).filter(
        or_(Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id)
    ).order_by(Message.timestamp.desc()).all()
    seen = set()
    partners = []
    for msg in all_convos:
        other_id = msg.receiver_id if msg.sender_id == current_user.id else msg.sender_id
        if other_id not in seen and other_id not in excluded:
            seen.add(other_id)
            u = User.query.get(other_id)
            last_msg = Message.query.filter(
                or_(
                    (Message.sender_id == current_user.id) & (Message.receiver_id == other_id),
                    (Message.sender_id == other_id) & (Message.receiver_id == current_user.id)
                )
            ).order_by(Message.timestamp.desc()).first()
            unread = Message.query.filter_by(
                sender_id=other_id,
                receiver_id=current_user.id,
                is_read=False
            ).count()
            partners.append({"user": u, "last_msg": last_msg, "unread": unread})
    return render_template("conversation.html",
        other=other, messages=messages,
        partners=partners, active_user=other)

@main.route("/messages/send-media/<username>", methods=["POST"])
@login_required
def send_media_message(username):
    other = User.query.filter_by(username=username).first_or_404()
    if not current_user.is_mutual_follow(other):
        return jsonify({"error": "Not mutual followers"}), 403
    file = request.files.get("media")
    if not file or not file.filename:
        return jsonify({"error": "No file"}), 400
    ext = file.filename.rsplit('.', 1)[-1].lower()
    allowed_img = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    allowed_audio = {'webm', 'mp3', 'ogg', 'm4a', 'wav'}
    if ext in allowed_img:
        msg_type = 'image'
        filename = secure_filename(
            f"dm_img_{current_user.id}_{int(datetime.utcnow().timestamp())}.{ext}"
        )
    elif ext in allowed_audio:
        msg_type = 'audio'
        filename = secure_filename(
            f"dm_audio_{current_user.id}_{int(datetime.utcnow().timestamp())}.{ext}"
        )
    else:
        return jsonify({"error": "File type not allowed"}), 400
    upload_folder = os.path.join(current_app.root_path, "static", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    file.save(os.path.join(upload_folder, filename))
    msg = Message(
        sender_id=current_user.id,
        receiver_id=other.id,
        content=f"{'📷 Photo' if msg_type == 'image' else '🎤 Voice message'}",
        image=filename if msg_type == 'image' else None,
        audio=filename if msg_type == 'audio' else None
    )
    db.session.add(msg)
    db.session.commit()
    socketio.emit("new_message", {
        "id": msg.id,
        "content": msg.content,
        "sender_username": current_user.username,
        "sender_display": current_user.display_name or current_user.username,
        "sender_pic": current_user.profile_pic,
        "timestamp": msg.timestamp.strftime("%H:%M"),
        "is_mine": False,
        "msg_type": msg_type,
        "media_url": f"/static/uploads/{filename}"
    }, room=f"user_{other.id}")
    return jsonify({
        "success": True,
        "msg_type": msg_type,
        "media_url": f"/static/uploads/{filename}",
        "timestamp": msg.timestamp.strftime("%H:%M")
    })

@main.route("/capsule/create", methods=["GET", "POST"])
@login_required
def create_capsule():
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        unlock_date_str = request.form.get("unlock_at", "").strip()
        image_filename = None
        if not content:
            flash("Capsule cannot be empty.", "danger")
            return redirect(url_for("main.create_capsule"))
        if not unlock_date_str:
            flash("Please pick an unlock date.", "danger")
            return redirect(url_for("main.create_capsule"))
        try:
            unlock_at = datetime.strptime(unlock_date_str, "%Y-%m-%d")
            if unlock_at <= datetime.utcnow():
                flash("Unlock date must be in the future.", "danger")
                return redirect(url_for("main.create_capsule"))
        except ValueError:
            flash("Invalid date format.", "danger")
            return redirect(url_for("main.create_capsule"))
        file = request.files.get("image")
        if file and file.filename != "" and allowed_file(file.filename):
            image_filename = secure_filename(
                f"capsule_{current_user.id}_{int(datetime.utcnow().timestamp())}_{file.filename}"
            )
            upload_folder = os.path.join(current_app.root_path, "static", "uploads")
            os.makedirs(upload_folder, exist_ok=True)
            file.save(os.path.join(upload_folder, image_filename))
        capsule = MemoryCapsule(user_id=current_user.id, content=content, image=image_filename, unlock_at=unlock_at)
        db.session.add(capsule)
        db.session.commit()
        flash(f"🔒 Memory capsule sealed! It opens on {capsule.unlock_date_str()}.", "success")
        return redirect(url_for("main.my_capsules"))
    min_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    max_date = (datetime.utcnow() + timedelta(days=365*5)).strftime("%Y-%m-%d")
    return render_template("create_capsule.html", min_date=min_date, max_date=max_date)


@main.route("/capsules")
@login_required
def my_capsules():
    unlock_capsules()
    capsules = MemoryCapsule.query.filter_by(user_id=current_user.id).order_by(MemoryCapsule.unlock_at.asc()).all()
    return render_template("capsules.html", capsules=capsules)


# ── PHASE 19 ROUTES ──────────────────────────────────────────────

@main.route("/block/<username>", methods=["POST"])
@login_required
def block_user(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user:
        return redirect(request.referrer or url_for("main.home"))
    if not current_user.is_blocking(user):
        db.session.add(Block(blocker_id=current_user.id, blocked_id=user.id))
        current_user.unfollow(user)
        user.unfollow(current_user)
        db.session.commit()
        flash(f"You blocked {username}.", "info")
    return redirect(url_for("main.home"))


@main.route("/unblock/<username>", methods=["POST"])
@login_required
def unblock_user(username):
    user = User.query.filter_by(username=username).first_or_404()
    block = Block.query.filter_by(blocker_id=current_user.id, blocked_id=user.id).first()
    if block:
        db.session.delete(block)
        db.session.commit()
        flash(f"You unblocked {username}.", "success")
    return redirect(request.referrer or url_for("main.settings"))


@main.route("/mute/<username>", methods=["POST"])
@login_required
def mute_user(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user:
        return redirect(request.referrer or url_for("main.home"))
    if not current_user.is_muting(user):
        db.session.add(Mute(muter_id=current_user.id, muted_id=user.id))
        db.session.commit()
        flash(f"You muted {username}.", "info")
    return redirect(request.referrer or url_for("main.profile", username=username))


@main.route("/unmute/<username>", methods=["POST"])
@login_required
def unmute_user(username):
    user = User.query.filter_by(username=username).first_or_404()
    mute = Mute.query.filter_by(muter_id=current_user.id, muted_id=user.id).first()
    if mute:
        db.session.delete(mute)
        db.session.commit()
        flash(f"You unmuted {username}.", "success")
    return redirect(request.referrer or url_for("main.settings"))


@main.route("/report/post/<int:post_id>", methods=["GET", "POST"])
@login_required
def report_post(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        description = request.form.get("description", "").strip()
        if not reason:
            flash("Please select a reason.", "danger")
            return redirect(url_for("main.report_post", post_id=post_id))
        existing = Report.query.filter_by(reporter_id=current_user.id, post_id=post_id).first()
        if existing:
            flash("You've already reported this post.", "info")
        else:
            db.session.add(Report(reporter_id=current_user.id, post_id=post_id,
                                  reported_id=post.user_id, reason=reason, description=description))
            db.session.commit()
            flash("Report submitted. Thank you for keeping Lynk safe. 🙏", "success")
        return redirect(url_for("main.comment_post", post_id=post_id))
    return render_template("report.html", target=post, target_type="post")


@main.route("/report/user/<username>", methods=["GET", "POST"])
@login_required
def report_user(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user:
        flash("You can't report yourself.", "danger")
        return redirect(url_for("main.profile", username=username))
    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        description = request.form.get("description", "").strip()
        if not reason:
            flash("Please select a reason.", "danger")
            return redirect(url_for("main.report_user", username=username))
        existing = Report.query.filter_by(reporter_id=current_user.id, reported_id=user.id, post_id=None).first()
        if existing:
            flash("You've already reported this user.", "info")
        else:
            db.session.add(Report(reporter_id=current_user.id, reported_id=user.id,
                                  reason=reason, description=description))
            db.session.commit()
            flash("Report submitted. Thank you. 🛡️", "success")
        return redirect(url_for("main.profile", username=username))
    return render_template("report.html", target=user, target_type="user")


@main.route("/account/pause", methods=["POST"])
@login_required
def pause_account():
    current_user.is_paused = True
    db.session.commit()
    logout_user()
    flash("Your account is paused. ⏸️", "info")
    return redirect(url_for("main.landing"))


@main.route("/account/unpause", methods=["POST"])
@login_required
def unpause_account():
    current_user.is_paused = False
    db.session.commit()
    flash("Welcome back! 🎉", "success")
    return redirect(url_for("main.home"))


@main.route("/settings/comment-permission", methods=["POST"])
@login_required
def set_comment_permission():
    perm = request.form.get("comment_permission", "everyone")
    if perm not in ("everyone", "followers", "nobody"):
        perm = "everyone"
    current_user.comment_permission = perm
    db.session.commit()
    flash(f"Comment permission updated to '{perm}'.", "success")
    return redirect(url_for("main.settings"))


@main.route("/profile-viewers")
@login_required
def profile_viewers():
    viewers = current_user.get_recent_profile_viewers(days=7)
    return render_template("profile_viewers.html", viewers=viewers)


# ── PHASE 20 — AI AGENTS ─────────────────────────────────────────

@main.route("/ai")
@login_required
def ai_chat():
    return render_template("ai.html")

@main.route("/ai/dm")
@login_required
def ai_dm():
    return render_template("ai_dm.html")

@main.route("/ai/chat", methods=["POST"])
@login_required
def ai_chat_send():
    data = request.get_json()
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "No messages provided"}), 400
    messages = messages[-20:]
    valid = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if not valid:
        return jsonify({"error": "Invalid messages"}), 400
    try:
        client = get_groq()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=1024,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are Lynk AI — a friendly, witty, and helpful assistant "
                        f"built into Lynk, a social networking app. "
                        f"The user's name is {current_user.display_name or current_user.username}. "
                        f"Keep responses concise and conversational. Use emojis occasionally."
                    )
                }
            ] + valid,
        )
        return jsonify({"reply": resp.choices[0].message.content})
    except Exception as e:
        print(f"AI CHAT ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@main.route("/ai/caption", methods=["POST"])
@login_required
def ai_caption():
    data = request.get_json()
    topic = data.get("topic", "").strip()
    if not topic:
        return jsonify({"error": "No topic provided"}), 400
    try:
        client = get_groq()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[{"role": "user", "content": (
                f"Generate 3 short engaging social media captions for a post about: {topic}\n"
                f"- Each caption max 2 sentences\n"
                f"- Include 2-3 hashtags at the end of each\n"
                f"- Separate each caption with a blank line\n"
                f"- No numbering, no labels, just the captions\n"
                f"- Mix tones: one casual, one inspirational, one fun"
            )}]
        )
        captions_text = resp.choices[0].message.content.strip()
        captions = [c.strip() for c in captions_text.split("\n\n") if c.strip()]
        return jsonify({"captions": captions[:3]})
    except Exception as e:
        print(f"AI CAPTION ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@main.route("/ai/bio", methods=["POST"])
@login_required
def ai_bio():
    data = request.get_json()
    interests = data.get("interests", "").strip()
    username = current_user.display_name or current_user.username
    try:
        client = get_groq()
        prompt = f"Write 3 different short bio options for a social media profile.\nUsername: {username}\n"
        if interests:
            prompt += f"Interests: {interests}\n"
        prompt += (
            "- Each bio under 150 characters\n"
            "- First-person voice\n"
            "- Include 1-2 emojis per bio\n"
            "- Separate each bio with a blank line\n"
            "- No numbering, just the bios"
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        bios_text = resp.choices[0].message.content.strip()
        bios = [b.strip() for b in bios_text.split("\n\n") if b.strip()]
        return jsonify({"bios": bios[:3]})
    except Exception as e:
        print(f"AI BIO ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@main.route("/ai/hashtags", methods=["POST"])
@login_required
def ai_hashtags():
    data = request.get_json()
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "No content provided"}), 400
    try:
        client = get_groq()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"Suggest 8 relevant hashtags for this social media post:\n\n{content}\n\n"
                f"- Return ONLY hashtags, one per line\n"
                f"- Include the # symbol\n"
                f"- No explanations, no numbering"
            )}]
        )
        tags_text = resp.choices[0].message.content.strip()
        tags = [t.strip() for t in tags_text.split("\n") if t.strip().startswith("#")]
        return jsonify({"hashtags": tags[:8]})
    except Exception as e:
        print(f"AI HASHTAGS ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@main.route("/ai/smart-reply", methods=["POST"])
@login_required
def ai_smart_reply():
    data = request.get_json()
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "No message provided"}), 400
    try:
        client = get_groq()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"Someone sent this message in a private chat: \"{message}\"\n\n"
                f"Generate exactly 3 short, natural reply suggestions.\n"
                f"Rules:\n"
                f"- Each reply max 8 words\n"
                f"- Casual, conversational tone\n"
                f"- One per line, no numbering, no quotes\n"
                f"- Mix tones: one warm, one casual, one funny/witty\n"
                f"- Just the reply text, nothing else"
            )}]
        )
        text = resp.choices[0].message.content.strip()
        replies = [r.strip() for r in text.split("\n") if r.strip()][:3]
        return jsonify({"replies": replies})
    except Exception as e:
        print(f"SMART REPLY ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@main.route("/ai/post-ideas", methods=["POST"])
@login_required
def ai_post_ideas():
    data = request.get_json()
    mood = data.get("mood", "").strip()
    interests = data.get("interests", "").strip()
    username = current_user.display_name or current_user.username
    try:
        client = get_groq()
        prompt = (
            f"Generate 5 short social media post ideas for a user named {username}.\n"
        )
        if mood:
            prompt += f"Their current mood: {mood}\n"
        if interests:
            prompt += f"Their interests: {interests}\n"
        prompt += (
            "Rules:\n"
            "- Each idea is ONE sentence max, under 120 chars\n"
            "- Make them specific and interesting, not generic\n"
            "- No hashtags, no numbering, no bullet points\n"
            "- Each idea on its own line\n"
            "- Mix styles: one funny, one deep, one casual, one observational, one personal\n"
            "- Just the ideas, nothing else"
        )
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.choices[0].message.content.strip()
        ideas = [i.strip() for i in text.split("\n") if i.strip()][:5]
        return jsonify({"ideas": ideas})
    except Exception as e:
        print(f"POST IDEAS ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@main.route("/share/post/<int:post_id>", methods=["POST"])
@login_required
def share_post_dm(post_id):
    post = Post.query.get_or_404(post_id)
    recipient_username = request.form.get("recipient", "").strip()
    if not recipient_username:
        return jsonify({"error": "No recipient"}), 400
    recipient = User.query.filter_by(username=recipient_username).first()
    if not recipient:
        return jsonify({"error": "User not found"}), 404
    if not current_user.is_mutual_follow(recipient):
        return jsonify({"error": "Not mutual followers"}), 403
    if current_user.is_blocking(recipient) or recipient.is_blocking(current_user):
        return jsonify({"error": "Blocked"}), 403

    # Create a post_share type message
    preview = (post.content or "📷 Photo")[:100]
    author = post.author.display_name or post.author.username
    msg = Message(
        sender_id=current_user.id,
        receiver_id=recipient.id,
        content=f"Shared a post by @{author}: {preview}",
        message_type='post_share',
        shared_post_id=post.id
    )
    db.session.add(msg)
    db.session.commit()
    socketio.emit("message_notification", {
        "from_username": current_user.username,
        "from_display": current_user.display_name or current_user.username,
        "unread": Message.query.filter_by(
            sender_id=current_user.id,
            receiver_id=recipient.id,
            is_read=False
        ).count()
    }, room=f"user_{recipient.id}")
    return jsonify({"success": True})

@main.route("/api/mutual-friends")
@login_required
def api_mutual_friends():
    mutual = []
    following_ids = [
        f.followed_id for f in
        current_user.following.filter_by(status="accepted").all()
    ]
    for uid in following_ids:
        user = User.query.get(uid)
        if not user or user.is_agent or user.id == current_user.id:
            continue
        follows_back = Follow.query.filter_by(
            follower_id=uid,
            followed_id=current_user.id,
            status="accepted"
        ).first()
        if not follows_back:
            continue
        if user.profile_pic and user.profile_pic != "default.jpg":
            pic = "/static/uploads/" + user.profile_pic
        else:
            pic = "/static/img/default_avatar.svg"
        mutual.append({
            "username": user.username,
            "display": user.display_name or user.username,
            "pic": pic
        })
    return jsonify({"friends": mutual})

# ── PHASE 22 — ADMIN PANEL ───────────────────────────────────

@main.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    from sqlalchemy import func
    total_users = User.query.filter_by(is_agent=False).count()
    total_posts = Post.query.count()
    total_reports = Report.query.filter_by(status="pending").count()
    total_agents = User.query.filter_by(is_agent=True).count()
    banned_users = User.query.filter_by(is_banned=True).count()
    since_7d = datetime.utcnow() - timedelta(days=7)
    new_users_week = User.query.filter(
        User.created_at >= since_7d,
        User.is_agent == False
    ).count()
    new_posts_week = Post.query.filter(
        Post.timestamp >= since_7d
    ).count()
    recent_reports = Report.query.filter_by(
        status="pending"
    ).order_by(Report.created_at.desc()).limit(5).all()
    recent_users = User.query.filter_by(
        is_agent=False
    ).order_by(User.created_at.desc()).limit(5).all()
    return render_template("admin/dashboard.html",
        total_users=total_users,
        total_posts=total_posts,
        total_reports=total_reports,
        total_agents=total_agents,
        banned_users=banned_users,
        new_users_week=new_users_week,
        new_posts_week=new_posts_week,
        recent_reports=recent_reports,
        recent_users=recent_users
    )


@main.route("/admin/users")
@login_required
@admin_required
def admin_users():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    query = User.query.filter_by(is_agent=False)
    if search:
        query = query.filter(User.username.ilike(f"%{search}%"))
    users = query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template("admin/users.html", users=users, search=search)


@main.route("/admin/users/<int:user_id>/ban", methods=["POST"])
@login_required
@admin_required
def admin_ban_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash("Cannot ban another admin.", "danger")
        return redirect(url_for("main.admin_users"))
    reason = request.form.get("reason", "Violation of community guidelines").strip()
    user.is_banned = True
    user.ban_reason = reason
    db.session.commit()
    flash(f"@{user.username} has been banned.", "success")
    return redirect(url_for("main.admin_users"))


@main.route("/admin/users/<int:user_id>/unban", methods=["POST"])
@login_required
@admin_required
def admin_unban_user(user_id):
    user = User.query.get_or_404(user_id)
    user.is_banned = False
    user.ban_reason = None
    db.session.commit()
    flash(f"@{user.username} has been unbanned.", "success")
    return redirect(url_for("main.admin_users"))


@main.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        flash("Cannot delete another admin.", "danger")
        return redirect(url_for("main.admin_users"))
    db.session.delete(user)
    db.session.commit()
    flash("User deleted permanently.", "success")
    return redirect(url_for("main.admin_users"))


@main.route("/admin/reports")
@login_required
@admin_required
def admin_reports():
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status", "pending")
    query = Report.query
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    reports = query.order_by(Report.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template("admin/reports.html",
        reports=reports, status_filter=status_filter
    )


@main.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@login_required
@admin_required
def admin_resolve_report(report_id):
    report = Report.query.get_or_404(report_id)
    report.status = "resolved"
    db.session.commit()
    flash("Report marked as resolved.", "success")
    return redirect(url_for("main.admin_reports"))


@main.route("/admin/reports/<int:report_id>/dismiss", methods=["POST"])
@login_required
@admin_required
def admin_dismiss_report(report_id):
    report = Report.query.get_or_404(report_id)
    report.status = "dismissed"
    db.session.commit()
    flash("Report dismissed.", "info")
    return redirect(url_for("main.admin_reports"))


@main.route("/admin/reports/<int:report_id>/delete-post", methods=["POST"])
@login_required
@admin_required
def admin_delete_reported_post(report_id):
    report = Report.query.get_or_404(report_id)
    if report.post_id:
        post = Post.query.get(report.post_id)
        if post:
            db.session.delete(post)
    report.status = "resolved"
    db.session.commit()
    flash("Post deleted and report resolved.", "success")
    return redirect(url_for("main.admin_reports"))


@main.route("/admin/posts")
@login_required
@admin_required
def admin_posts():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    flagged_only = request.args.get("flagged") == "1"
    query = Post.query
    if search:
        query = query.filter(Post.content.ilike(f"%{search}%"))
    if flagged_only:
        query = query.filter(Post.is_flagged == True)
    posts = query.order_by(Post.timestamp.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    return render_template("admin/posts.html", posts=posts, search=search, flagged_only=flagged_only)
    


@main.route("/admin/posts/<int:post_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    flash("Post deleted.", "success")
    return redirect(url_for("main.admin_posts"))

def send_weekly_digest(app):
    with app.app_context():
        from app.models import User, Post, Follow, Like, Notification
        from app.extensions import db, mail
        from flask_mail import Message as MailMessage
        from groq import Groq
        from datetime import datetime, timedelta
        import os

        now = datetime.utcnow()
        since = now - timedelta(days=7)

        users = User.query.filter_by(
            is_verified=True,
            is_paused=False,
            is_agent=False
        ).all()

        for user in users:
            try:
                # Gather user's week data
                new_followers = Follow.query.filter(
                    Follow.followed_id == user.id,
                    Follow.status == 'accepted',
                    Follow.timestamp >= since
                ).count()

                new_notifications = Notification.query.filter(
                    Notification.user_id == user.id,
                    Notification.timestamp >= since
                ).count()

                recent_posts = Post.query.filter(
                    Post.user_id == user.id,
                    Post.timestamp >= since
                ).all()

                total_likes_this_week = sum(
                    Like.query.filter(
                        Like.post_id == p.id
                    ).count() for p in recent_posts
                )

                # Get trending posts from their feed
                followed_ids = [
                    f.followed_id for f in
                    user.following.filter_by(status='accepted').all()
                ]
                trending = []
                if followed_ids:
                    from sqlalchemy import func
                    trending = db.session.query(
                        Post, func.count(Like.id).label('lc')
                    ).join(Like, Like.post_id == Post.id)\
                     .filter(
                         Post.user_id.in_(followed_ids),
                         Post.timestamp >= since,
                         Post.is_private == False
                     ).group_by(Post.id)\
                      .order_by(func.count(Like.id).desc())\
                      .limit(3).all()

                # Build digest summary with Groq
                context = (
                    f"User: {user.display_name or user.username}\n"
                    f"This week on LYNK:\n"
                    f"- New followers: {new_followers}\n"
                    f"- Notifications received: {new_notifications}\n"
                    f"- Posts made: {len(recent_posts)}\n"
                    f"- Likes received on posts: {total_likes_this_week}\n"
                )

                client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
                resp = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    max_tokens=200,
                    messages=[{"role": "user", "content": (
                        f"Write a short, warm, encouraging weekly summary for a social media user.\n"
                        f"{context}\n"
                        f"2-3 sentences max. Friendly tone. Mention their actual numbers. "
                        f"End with one motivational sentence for next week. No emojis in first sentence."
                    )}]
                )
                summary = resp.choices[0].message.content.strip()

                # Build trending posts HTML
                trending_html = ""
                for post, lc in trending:
                    author = post.author.display_name or post.author.username
                    preview = (post.content or '')[:80]
                    trending_html += f"""
                    <div style="background:#f8f0f6;border-radius:12px;
                                padding:12px 16px;margin-bottom:10px;">
                        <div style="font-size:12px;font-weight:700;
                                    color:#9b6fd4;margin-bottom:4px;">
                            @{author} · ❤️ {lc} likes
                        </div>
                        <div style="font-size:14px;color:#2d1f2e;line-height:1.5;">
                            {preview}{'…' if len(post.content or '') > 80 else ''}
                        </div>
                    </div>
                    """

                # Send email
                msg = MailMessage(
                    subject=f"Your LYNK week in review ✨",
                    recipients=[user.email],
                    html=f"""
                    <div style="font-family:'Nunito',sans-serif;max-width:520px;
                                margin:auto;padding:0;background:#fdf6fb;">

                        <!-- Header -->
                        <div style="background:linear-gradient(135deg,#9b6fd4,#e991c0);
                                    padding:32px 32px 24px;border-radius:20px 20px 0 0;
                                    text-align:center;">
                            <div style="font-family:serif;font-size:28px;font-weight:700;
                                        color:white;letter-spacing:-0.5px;">
                                Lynk ✦
                            </div>
                            <div style="color:rgba(255,255,255,.85);font-size:14px;
                                        margin-top:6px;font-weight:500;">
                                Your weekly digest
                            </div>
                        </div>

                        <!-- Body -->
                        <div style="background:white;padding:28px 32px;
                                    border:1px solid #edd9ea;border-top:none;">

                            <p style="font-size:16px;color:#2d1f2e;font-weight:600;
                                       margin-bottom:6px;">
                                Hey {user.display_name or user.username} 👋
                            </p>
                            <p style="font-size:14px;color:#6b5a70;line-height:1.7;
                                       margin-bottom:24px;">
                                {summary}
                            </p>

                            <!-- Stats row -->
                            <div style="display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap;">
                                <div style="flex:1;min-width:100px;background:#f8f0f6;
                                            border-radius:14px;padding:16px;text-align:center;">
                                    <div style="font-size:26px;font-weight:900;
                                                color:#9b6fd4;">{new_followers}</div>
                                    <div style="font-size:11px;color:#9b7ea0;
                                                font-weight:700;text-transform:uppercase;
                                                letter-spacing:.5px;margin-top:3px;">
                                        New Followers
                                    </div>
                                </div>
                                <div style="flex:1;min-width:100px;background:#f8f0f6;
                                            border-radius:14px;padding:16px;text-align:center;">
                                    <div style="font-size:26px;font-weight:900;
                                                color:#e991c0;">{total_likes_this_week}</div>
                                    <div style="font-size:11px;color:#9b7ea0;
                                                font-weight:700;text-transform:uppercase;
                                                letter-spacing:.5px;margin-top:3px;">
                                        Likes Received
                                    </div>
                                </div>
                                <div style="flex:1;min-width:100px;background:#f8f0f6;
                                            border-radius:14px;padding:16px;text-align:center;">
                                    <div style="font-size:26px;font-weight:900;
                                                color:#7db8e8;">{len(recent_posts)}</div>
                                    <div style="font-size:11px;color:#9b7ea0;
                                                font-weight:700;text-transform:uppercase;
                                                letter-spacing:.5px;margin-top:3px;">
                                        Posts Made
                                    </div>
                                </div>
                            </div>

                            <!-- Trending -->
                            {f'''
                            <div style="margin-bottom:24px;">
                                <div style="font-size:13px;font-weight:800;color:#2d1f2e;
                                            text-transform:uppercase;letter-spacing:.6px;
                                            margin-bottom:12px;">
                                    🔥 Trending in your feed
                                </div>
                                {trending_html}
                            </div>
                            ''' if trending_html else ''}

                            <!-- CTA -->
                            <div style="text-align:center;margin-top:8px;">
                                <a href="http://localhost:5000/home"
                                   style="display:inline-block;
                                          background:linear-gradient(135deg,#9b6fd4,#e991c0);
                                          color:white;padding:13px 32px;border-radius:14px;
                                          text-decoration:none;font-weight:700;font-size:15px;
                                          box-shadow:0 4px 16px rgba(155,111,212,.3);">
                                    Open LYNK ✦
                                </a>
                            </div>
                        </div>

                        <!-- Footer -->
                        <div style="background:#f8f0f6;padding:16px 32px;
                                    border-radius:0 0 20px 20px;
                                    border:1px solid #edd9ea;border-top:none;
                                    text-align:center;">railway run python finalcheck.py
                                    
                            <p style="font-size:12px;color:#9b7ea0;margin:0;">
                                You're receiving this because you have a LYNK account.<br>
                                Sent every Monday · LYNK ✦
                            </p>
                        </div>

                    </div>
                    """
                )
                mail.send(msg)
                print(f"✅ Digest sent to @{user.username}")

            except Exception as e:
                print(f"Digest error for @{user.username}: {e}")
                continue


@main.route("/admin/send-digest", methods=["POST"])
@login_required
@admin_required
def admin_send_digest():
    import threading
    thread = threading.Thread(
        target=send_weekly_digest,
        args=(current_app._get_current_object(),),
        daemon=True
    )
    thread.start()
    flash("📧 Weekly digest sending in background!", "success")
    return redirect(url_for("main.admin_dashboard"))
# ── SOCKETIO EVENTS ──────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    if current_user.is_authenticated:
        join_room(f"user_{current_user.id}")

@socketio.on("disconnect")
def on_disconnect():
    if current_user.is_authenticated:
        leave_room(f"user_{current_user.id}")

@socketio.on("send_message")
def handle_message(data):
    if not current_user.is_authenticated:
        return
    receiver_username = data.get("receiver")
    content = data.get("content", "").strip()
    if not content or not receiver_username:
        return
    receiver = User.query.filter_by(username=receiver_username).first()
    if not receiver:
        return
    if not current_user.is_mutual_follow(receiver):
        return
    if current_user.is_blocking(receiver) or receiver.is_blocking(current_user):
        return
    msg = Message(
        sender_id=current_user.id,
        receiver_id=receiver.id,
        content=content
    )
    db.session.add(msg)
    db.session.commit()
    msg_data = {
        "id": msg.id,
        "content": msg.content,
        "sender_username": current_user.username,
        "sender_display": current_user.display_name or current_user.username,
        "sender_pic": current_user.profile_pic,
        "timestamp": msg.timestamp.strftime("%H:%M"),
        "is_mine": False,
        "msg_type": "text",
        "media_url": None
    }
    emit("new_message", msg_data, room=f"user_{receiver.id}")
    msg_data["is_mine"] = True
    emit("new_message", msg_data, room=f"user_{current_user.id}")


@socketio.on("typing")
def handle_typing(data):
    if not current_user.is_authenticated:
        return
    receiver = User.query.filter_by(username=data.get("receiver")).first()
    if receiver:
        emit("user_typing", {"username": current_user.username,
             "display": current_user.display_name or current_user.username},
             room=f"user_{receiver.id}")


@socketio.on("stop_typing")
def handle_stop_typing(data):
    if not current_user.is_authenticated:
        return
    receiver = User.query.filter_by(username=data.get("receiver")).first()
    if receiver:
        emit("user_stop_typing", {}, room=f"user_{receiver.id}")


@main.route('/agents')
def agent_marketplace():
    page = request.args.get('page', 1, type=int)
    sort = request.args.get('sort', 'popular')
    query = User.query.filter_by(is_agent=True, agent_is_active=True)
    if sort == 'newest':
        query = query.order_by(User.created_at.desc())
    elif sort == 'active':
        query = query.order_by(User.agent_last_posted.desc().nullslast())
    else:
        from sqlalchemy import func
        follower_counts = (
            db.session.query(Follow.followed_id, func.count(Follow.id).label('cnt'))
            .filter(Follow.status == 'accepted')
            .group_by(Follow.followed_id)
            .subquery()
        )
        query = (query
                 .outerjoin(follower_counts, User.id == follower_counts.c.followed_id)
                 .order_by(follower_counts.c.cnt.desc().nullslast()))
    agents = query.paginate(page=page, per_page=12, error_out=False)
    templates = AgentTemplate.query.all()
    return render_template('agent_marketplace.html', agents=agents, templates=templates, sort=sort)


@main.route('/agents/create', methods=['GET', 'POST'])
@login_required
def create_agent():
    existing = User.query.filter_by(agent_owner_id=current_user.id, is_agent=True).count()
    if existing >= 3:
        flash('You can only create up to 3 AI agents.', 'warning')
        return redirect(url_for('main.manage_my_agents'))
    templates = AgentTemplate.query.all()
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        display_name = request.form.get('display_name', '').strip()
        personality = request.form.get('personality', '').strip()
        content_type = request.form.get('content_type', '').strip()
        frequency = request.form.get('frequency', 6, type=int)
        template_id = request.form.get('template_id', type=int)

        if not username or not display_name:
            flash('Username and display name are required.', 'danger')
            return render_template('create_agent.html', templates=templates)

        if User.query.filter_by(username=username).first():
            flash('That username is already taken.', 'danger')
            return render_template('create_agent.html', templates=templates)

        if not (1 <= frequency <= 24):
            frequency = 6

        if template_id:
            tmpl = AgentTemplate.query.get(template_id)
            if tmpl:
                personality = tmpl.personality
                content_type = tmpl.content_type

        if not personality or not content_type:
            flash('Personality and content type are required.', 'danger')
            return render_template('create_agent.html', templates=templates)

        agent = User(
            username=username,
            display_name=display_name,
            email=f'{username}@agent.lynk.internal',
            password='',
            is_verified=True,
            is_agent=True,
            agent_personality=personality,
            agent_content_type=content_type,
            agent_owner_id=current_user.id,
            agent_posting_frequency=frequency,
            agent_is_active=True,
            agent_post_count=0,
        )

        avatar = request.files.get('avatar')
        if avatar and avatar.filename:
            allowed_exts = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
            ext = avatar.filename.rsplit('.', 1)[-1].lower()
            if ext in allowed_exts:
                fname = f'agent_{username}_{int(datetime.utcnow().timestamp())}.{ext}'
                upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
                os.makedirs(upload_folder, exist_ok=True)
                avatar.save(os.path.join(upload_folder, fname))
                agent.profile_pic = fname

        db.session.add(agent)
        db.session.commit()

        try:
            client = get_groq()
            resp = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content":
                    f"You are {display_name}, a new AI agent on LYNK social network. "
                    f"Personality: {personality[:200]}. "
                    f"Write a short, fun introduction post (1-2 sentences, max 200 chars). "
                    f"Be yourself. No hashtags. Just the text."}],
                max_tokens=80
            )
            intro = resp.choices[0].message.content.strip()
            intro_post = Post(content=intro, user_id=agent.id)
            db.session.add(intro_post)
            agent.agent_post_count = 1
            agent.agent_last_posted = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            print(f"Intro post error: {e}")

        flash(f'Agent @{username} created successfully! 🤖', 'success')
        return redirect(url_for('main.profile', username=username))

    return render_template('create_agent.html', templates=templates)


@main.route('/agents/mine')
@login_required
def manage_my_agents():
    agents = User.query.filter_by(
        agent_owner_id=current_user.id, is_agent=True
    ).order_by(User.created_at.desc()).all()
    now = datetime.utcnow()
    return render_template('manage_agents.html', agents=agents, now=now)


@main.route('/agents/<int:agent_id>/toggle', methods=['POST'])
@login_required
def toggle_agent(agent_id):
    agent = User.query.get_or_404(agent_id)
    if agent.agent_owner_id != current_user.id:
        abort(403)
    agent.agent_is_active = not agent.agent_is_active
    db.session.commit()
    status = 'resumed' if agent.agent_is_active else 'paused'
    flash(f'Agent @{agent.username} has been {status}.', 'success')
    return redirect(url_for('main.manage_my_agents'))


@main.route('/agents/<int:agent_id>/delete', methods=['POST'])
@login_required
def delete_agent(agent_id):
    agent = User.query.get_or_404(agent_id)
    if agent.agent_owner_id != current_user.id:
        abort(403)
    Post.query.filter_by(user_id=agent.id).delete()
    Follow.query.filter(
        (Follow.follower_id == agent.id) | (Follow.followed_id == agent.id)
    ).delete()
    db.session.delete(agent)
    db.session.commit()
    flash('Agent deleted.', 'info')
    return redirect(url_for('main.manage_my_agents'))


@main.route('/agents/<int:agent_id>/post-now', methods=['POST'])
@login_required
def agent_post_now(agent_id):
    agent = User.query.get_or_404(agent_id)
    if agent.agent_owner_id != current_user.id:
        abort(403)
    if not agent.agent_is_active:
        flash('Agent is paused. Resume it first.', 'warning')
        return redirect(url_for('main.manage_my_agents'))
    try:
        client = get_groq()
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content":
                f"You are {agent.username}, an AI agent on a social network. "
                f"Your personality: {agent.agent_personality} "
                f"You post about: {agent.agent_content_type}. "
                f"Write ONE short social media post (1-3 sentences, max 240 chars). "
                f"NO hashtags unless they feel natural. NO quotes around the text. "
                f"Just write the post."}],
            max_tokens=120
        )
        content = resp.choices[0].message.content.strip()
        if len(content) > 500:
            content = content[:497] + "..."
        db.session.add(Post(content=content, user_id=agent.id))
        agent.agent_last_posted = datetime.utcnow()
        agent.agent_post_count = (agent.agent_post_count or 0) + 1
        db.session.commit()
        flash(f'Posted! "{content[:60]}..."', 'success')
    except Exception as e:
        flash(f'Post failed: {str(e)[:100]}', 'danger')
    return redirect(url_for('main.manage_my_agents'))


@main.route('/agents/<int:agent_id>/analytics')
@login_required
def agent_analytics(agent_id):
    agent = User.query.get_or_404(agent_id)
    if agent.agent_owner_id != current_user.id:
        abort(403)
    posts = (Post.query
             .filter_by(user_id=agent.id)
             .order_by(Post.timestamp.desc())
             .limit(20).all())
    follower_count = Follow.query.filter_by(followed_id=agent.id, status='accepted').count()
    top_posts = sorted(posts, key=lambda p: len(p.likes), reverse=True)[:5]
    return render_template('agent_analytics.html', agent=agent, posts=posts,
                           follower_count=follower_count, top_posts=top_posts)


@main.route('/feed/agents')
@login_required
def agent_feed():
    page = request.args.get('page', 1, type=int)
    followed_agent_ids = (
        db.session.query(Follow.followed_id)
        .filter(Follow.follower_id == current_user.id, Follow.status == 'accepted')
        .join(User, User.id == Follow.followed_id)
        .filter(User.is_agent == True)
        .subquery()
    )
    posts = (Post.query
             .join(User, User.id == Post.user_id)
             .filter(
                 User.is_agent == True,
                 Post.user_id.in_(followed_agent_ids),
                 Post.is_private == False
             )
             .order_by(Post.timestamp.desc())
             .paginate(page=page, per_page=10, error_out=False))
    return render_template('agent_feed.html', posts=posts)


def run_agent_conversation_scheduler(app):
    with app.app_context():
        import random
        from app.models import (User, Post, Comment, AgentRelationship,
                                AgentConversation, Notification)
        from app.extensions import db
        from groq import Groq
        import os
        from datetime import datetime, timedelta

        since = datetime.utcnow() - timedelta(hours=24)

        agent_posts = (
            Post.query
            .join(User, User.id == Post.user_id)
            .filter(
                User.is_agent == True,
                User.agent_is_active == True,
                Post.timestamp >= since,
                Post.is_private == False
            )
            .all()
        )

        if not agent_posts:
            print("Agent conversations: no eligible posts found.")
            return

        random.shuffle(agent_posts)

        for post in agent_posts[:5]:
            original_agent = User.query.get(post.user_id)
            if not original_agent:
                continue

            other_agents = (
                User.query
                .filter(
                    User.is_agent == True,
                    User.agent_is_active == True,
                    User.id != original_agent.id
                )
                .all()
            )

            if not other_agents:
                continue

            existing_convo = AgentConversation.query.filter_by(
                post_id=post.id
            ).filter(
                AgentConversation.created_at >= since
            ).first()

            if existing_convo:
                continue

            responder = None
            relationship_type = 'neutral'

            for candidate in other_agents:
                rel = AgentRelationship.query.filter(
                    (
                        (AgentRelationship.agent_a_id == original_agent.id) &
                        (AgentRelationship.agent_b_id == candidate.id)
                    ) | (
                        (AgentRelationship.agent_a_id == candidate.id) &
                        (AgentRelationship.agent_b_id == original_agent.id)
                    )
                ).first()
                if rel:
                    responder = candidate
                    relationship_type = rel.relationship_type
                    break

            if not responder:
                responder = random.choice(other_agents)
                relationship_type = 'neutral'

            if relationship_type == 'rival':
                stance = (
                    "You disagree with or have a different perspective on this post. "
                    "Be respectful but push back confidently. "
                    "Don't be rude — just make your opposing point clearly."
                )
            elif relationship_type == 'ally':
                stance = (
                    "You genuinely agree with this and want to build on it. "
                    "Add something meaningful, extend the thought, or share why it resonates."
                )
            else:
                stance = (
                    "React to this post naturally in your own voice. "
                    "You can agree, disagree, or add a new angle — whatever feels right."
                )

            try:
                client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

                round1_prompt = (
                    f"You are {responder.display_name or responder.username}, "
                    f"an AI personality on a social network.\n"
                    f"Your personality: {responder.agent_personality[:300]}\n\n"
                    f"You just saw this post by {original_agent.display_name or original_agent.username}:\n"
                    f'"{post.content}"\n\n'
                    f"{stance}\n\n"
                    f"Write a SHORT comment (1-2 sentences max, under 200 chars). "
                    f"Sound like yourself. No hashtags. No quotes around your reply. "
                    f"Just write the comment directly."
                )

                resp1 = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": round1_prompt}],
                    max_tokens=80
                )
                comment1_text = resp1.choices[0].message.content.strip()

                comment1 = Comment(
                    content=comment1_text,
                    user_id=responder.id,
                    post_id=post.id
                )
                db.session.add(comment1)
                db.session.flush()

                convo = AgentConversation(
                    post_id=post.id,
                    initiator_id=original_agent.id,
                    responder_id=responder.id,
                    round_count=1,
                    last_activity=datetime.utcnow()
                )
                db.session.add(convo)
                db.session.flush()

                if original_agent.agent_owner_id:
                    existing_notif = Notification.query.filter_by(
                        user_id=original_agent.agent_owner_id,
                        actor_id=responder.id,
                        type='agent_conversation',
                        post_id=post.id
                    ).first()
                    if not existing_notif:
                        db.session.add(Notification(
                            user_id=original_agent.agent_owner_id,
                            actor_id=responder.id,
                            type='agent_conversation',
                            post_id=post.id
                        ))

                db.session.commit()
                print(f"✅ Agent conversation: @{responder.username} replied to @{original_agent.username}")

                if random.random() < 0.6:
                    round2_prompt = (
                        f"You are {original_agent.display_name or original_agent.username}, "
                        f"an AI personality on a social network.\n"
                        f"Your personality: {original_agent.agent_personality[:300]}\n\n"
                        f"You posted: \"{post.content}\"\n\n"
                        f"{responder.display_name or responder.username} replied: "
                        f"\"{comment1_text}\"\n\n"
                        f"Write a SHORT reply (1-2 sentences, under 200 chars). "
                        f"Stay in character. Respond naturally — agree, push back, or redirect. "
                        f"No hashtags. Just the reply text."
                    )

                    resp2 = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": round2_prompt}],
                        max_tokens=80
                    )
                    comment2_text = resp2.choices[0].message.content.strip()

                    comment2 = Comment(
                        content=comment2_text,
                        user_id=original_agent.id,
                        post_id=post.id
                    )
                    db.session.add(comment2)

                    convo.round_count = 2
                    convo.last_activity = datetime.utcnow()

                    db.session.commit()
                    print(f"✅ Round 2: @{original_agent.username} replied back")

                break

            except Exception as e:
                print(f"Agent conversation error: {e}")
                db.session.rollback()
                continue


def start_conversation_scheduler(app):
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        from apscheduler.schedulers.background import BackgroundScheduler
        import atexit

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=lambda: run_agent_conversation_scheduler(app),
            trigger="interval",
            hours=2,
            id="agent_conversation_job",
            replace_existing=True
        )
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown())
        print("✅ Agent conversation scheduler started — runs every 2 hours.")
    else:
        print("⏳ Conversation scheduler waiting for reloader...")

def start_digest_scheduler(app):
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        from apscheduler.schedulers.background import BackgroundScheduler
        import atexit
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=lambda: send_weekly_digest(app),
            trigger="cron",
            day_of_week="mon",
            hour=9,
            minute=0,
            id="weekly_digest_job",
            replace_existing=True
        )
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown())
        print("✅ Weekly digest scheduler started — runs every Monday 9am.")
    else:
        print("⏳ Digest scheduler waiting for reloader...")
# ─────────────────────────────────────────────────────────────────────────────
# AGENT CONVERSATION ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@main.route('/conversations')
@login_required
def agent_conversations():
    from app.models import AgentConversation

    conversations = (
        AgentConversation.query
        .order_by(AgentConversation.last_activity.desc())
        .limit(20)
        .all()
    )

    convo_data = []
    for convo in conversations:
        post = convo.post
        if not post:
            continue

        agent_comments = (
            Comment.query
            .join(User, User.id == Comment.user_id)
            .filter(
                Comment.post_id == post.id,
                User.is_agent == True
            )
            .order_by(Comment.timestamp.asc())
            .all()
        )

        if agent_comments:
            convo_data.append({
                'conversation': convo,
                'post': post,
                'comments': agent_comments,
                'initiator': convo.initiator,
                'responder': convo.responder,
            })

    return render_template('agent_conversations.html', convo_data=convo_data)


@main.route('/conversations/trigger', methods=['POST'])
@login_required
def trigger_conversation():
    run_agent_conversation_scheduler(current_app._get_current_object())
    flash('🤖 Conversation engine triggered! Check back in a moment.', 'success')
    return redirect(url_for('main.agent_conversations'))


@main.route('/agents/relationship/set', methods=['POST'])
@login_required
def set_agent_relationship():
    from app.models import AgentRelationship

    agent_a_id = request.form.get('agent_a_id', type=int)
    agent_b_id = request.form.get('agent_b_id', type=int)
    rel_type   = request.form.get('relationship_type', 'neutral')

    if not agent_a_id or not agent_b_id or agent_a_id == agent_b_id:
        flash('Invalid relationship configuration.', 'danger')
        return redirect(url_for('main.manage_my_agents'))

    agent_a = User.query.get_or_404(agent_a_id)
    if agent_a.agent_owner_id != current_user.id:
        abort(403)

    if rel_type not in ('rival', 'ally', 'neutral'):
        rel_type = 'neutral'

    existing = AgentRelationship.query.filter(
        (
            (AgentRelationship.agent_a_id == agent_a_id) &
            (AgentRelationship.agent_b_id == agent_b_id)
        ) | (
            (AgentRelationship.agent_a_id == agent_b_id) &
            (AgentRelationship.agent_b_id == agent_a_id)
        )
    ).first()

    if existing:
        existing.relationship_type = rel_type
    else:
        db.session.add(AgentRelationship(
            agent_a_id=agent_a_id,
            agent_b_id=agent_b_id,
            relationship_type=rel_type
        ))

    db.session.commit()
    flash(f'Relationship set: {rel_type} ✅', 'success')
    return redirect(url_for('main.manage_my_agents'))

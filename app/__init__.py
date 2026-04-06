from flask import Flask, render_template
from .extensions import db, login_manager, mail, socketio, csrf

def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")
    app.config["DEBUG"] = True

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*")
    csrf.init_app(app)
    

    # ── ADD THESE TWO LINES ──
    from .models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))
    # ────────────────────────
    from .routes import main, init_template_filters
    app.register_blueprint(main)
    init_template_filters(app)

    with app.app_context():
        db.create_all()

        from .models import AgentTemplate, seed_agent_templates
        seed_agent_templates()

        from .models import User
        from werkzeug.security import generate_password_hash
        lynk_bot = User.query.filter_by(username="lynk_ai").first()
        if not lynk_bot:
            bot = User(
                username="lynk_ai",
                display_name="Lynk AI ⚡",
                email="bot@lynk.internal",
                password=generate_password_hash("internal-bot-no-login"),
                is_verified=True,
                is_agent=True,
                agent_personality=(
                    "You are the official Lynk AI — the voice of the Lynk social platform. "
                    "You post engaging, community-focused content. You celebrate users, share "
                    "platform tips, post motivational content, highlight interesting social trends, "
                    "and keep the community energized. You are friendly, inclusive, and positive. "
                    "Occasionally witty. Always genuine. Short punchy posts."
                ),
                agent_content_type="platform updates, community highlights, motivation, social trends",
                agent_posting_frequency=4,
                agent_is_active=True,
                agent_post_count=0
            )
            db.session.add(bot)
            db.session.commit()
            print("✅ Official Lynk AI bot created: @lynk_ai")

    # Start both schedulers
    from .routes import start_agent_scheduler, start_conversation_scheduler, start_digest_scheduler
    start_agent_scheduler(app)
    start_conversation_scheduler(app)
    start_digest_scheduler(app)
    
    # ── ERROR HANDLERS ──────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("500.html"), 500

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("404.html"), 403

    return app  


 
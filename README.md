# Lynk

**A full-stack social media platform built with Flask, featuring real-time messaging, ephemeral content, memory capsules, and a comprehensive trust & safety system.**

Lynk is a feature-rich social networking application that combines familiar social media patterns (posts, stories, follows, messaging) with unique features like time-locked memory capsules and disappearing "thoughts" — built entirely from scratch using Python, Flask, and Socket.IO.

---

## 🚀 Live Demo

**[web-production-cc865.up.railway.app](https://web-production-cc865.up.railway.app/)**

Deployed on Railway with PostgreSQL, real-time WebSocket support, and a full admin dashboard.
> Hosted on Railway's free tier — if the link is occasionally asleep/unresponsive due to platform limits, a full walkthrough video is available
<img width="1360" height="610" alt="20260622-1134-20 2703945" src="https://github.com/user-attachments/assets/1acba315-2764-4041-ac94-43bb4a20386e" />




---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Database Schema](#database-schema)
- [Installation](#installation)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Screenshots](#screenshots)
- [Roadmap](#roadmap)
- [License](#license)

---

## Overview

Lynk was built to demonstrate end-to-end full-stack development skills — from database design and authentication security to real-time communication and content moderation systems. Every feature was implemented from the ground up without relying on pre-built social media boilerplates, giving full control over the architecture and data model.

The project is structured in clearly defined development phases, allowing for incremental, testable feature delivery — a workflow well-suited to client-facing freelance projects.

---

## Features

### 🔐 Authentication & Security
- Email-verified registration with token-based confirmation links
- Two-Factor Authentication (2FA) via emailed one-time codes
- Account lockout after repeated failed login attempts (brute-force protection)
- Secure password hashing (Werkzeug)
- Login history tracking with device/browser detection
- Email change flow with confirmation tokens
- Session-wide logout ("log out all devices")
- Account deletion with password confirmation

### 📝 Posts & Content
- Rich text posts with image uploads
- @mentions and #hashtags with automatic link rendering
- Embedded polls (up to 4 options) with live vote percentages
- Public/private post visibility
- Post view tracking and engagement metrics
- Pin a post to your profile
- Edit and delete posts

### 💬 Engagement
- Likes and 6-emoji reaction system
- Threaded comments with mention support
- Real-time notification system (likes, comments, mentions, follows)
- Trending posts algorithm (24-hour engagement window)
- Trending hashtags computed from recent activity

### 👥 Social Graph
- Follow / unfollow with public and private account support
- Follow request approval flow for private accounts
- Followers / following lists
- Mutual-follow detection (gates direct messaging)
- User suggestions and "Explore" discovery page

### ⏳ Ephemeral Content
- **Stories** — 24-hour disappearing photo/text stories with view tracking and progress bar UI
- **Thoughts** — 6-hour disappearing micro-posts (no likes/comments, designed for low-pressure sharing)
- **Memory Capsules** — Write a message to your future self, lock it with a custom unlock date (1 day to 5 years out), and it automatically converts into a public post when the date arrives

### 💬 Real-Time Messaging
- One-to-one direct messaging gated by mutual follow
- Built with Flask-SocketIO for instant delivery
- Live typing indicators
- Read receipts (sent / delivered / seen)
- Unread message badges synced in real time across the app

### 🛡️ Trust & Safety
- **Block users** — full isolation: blocked users can't view your profile, follow you, message you, or see you in search
- **Mute users** — silently hide someone's posts from your feed without unfollowing or notifying them
- **Report system** — report posts or accounts across 7 categories (spam, harassment, inappropriate content, fake account, hate speech, misinformation, other)
- **Sensitive content warnings** — authors can flag posts with a blur overlay that viewers must tap through
- **Dynamic trust score (0–100)** — calculated from email verification, follower count, posting activity, mutual connections, and account age; reduced by reports received
- **Comment permissions** — restrict who can comment on your posts (everyone / followers only / nobody)
- **Profile view tracking** — see who viewed your profile in the last 7 days
- **Pause account** — temporarily deactivate without losing data; reactivates automatically on next login

### 🤖 AI Agents
- **Lynk AI Chat Assistant** — in-app conversational assistant powered by an LLM
- **Caption generator** — AI-generated caption suggestions for posts based on content/image
- **Bio writer** — AI-assisted profile bio generation
- **Hashtag recommender** — context-aware hashtag suggestions while composing a post
- **Smart replies** — quick AI-suggested responses in direct messages
- **Vibe analyzer / content tools** — automated content insights to assist posting decisions

### 🛠️ Admin Panel
- Centralized dashboard for platform oversight
- User management (view, search, suspend/restore accounts)
- Post moderation queue
- AI-assisted content flagging for review
- Platform-wide statistics and activity metrics
- Report review workflow (status: pending / reviewed / actioned)

### ✨ Polish
- Custom 404 / 500 error pages
- Loading skeletons and smooth transition states
- Refined empty states across every page
- Animation pass across interactive elements

### ☁️ Deployment
- Deployed on **Railway** with managed **PostgreSQL**
- Production-ready environment configuration
- SSL-secured custom domain

### 🎨 UI / UX
- 5 complete theme palettes (Sakura, Ocean, Ember, Forest, Midnight), each with light/dark variants
- Fully responsive layout with persistent theme preference (localStorage)
- Mood status indicator on profile
- Achievement badges (New Member, Veteran, Regular, Popular, Rising Star)
- Custom modal image viewer with engagement stats
- AJAX-driven interactions (likes, reactions, follows, poll voting) — no full page reloads

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python, Flask (Blueprint architecture) |
| **Database** | SQLAlchemy ORM, PostgreSQL (production) / SQLite (dev) |
| **Real-Time** | Flask-SocketIO |
| **AI / LLM** | LLM API integration for chat assistant, content generation, smart replies |
| **Auth** | Flask-Login, Werkzeug Security, itsdangerous (token signing) |
| **Email** | Flask-Mail (SMTP) |
| **Frontend** | Jinja2 templates, vanilla JavaScript, custom CSS (no framework) |
| **File Handling** | Werkzeug secure file uploads |
| **Deployment** | Railway, managed PostgreSQL, SSL |

No CSS or JS frameworks were used — all UI, theming, and interactivity were hand-built to maintain full control over performance and design.

---

## Architecture

Lynk follows the **Flask application factory pattern** with a modular blueprint structure:

```
create_app()
 ├── extensions.py   → db, login_manager, mail, socketio (initialized once, imported everywhere)
 ├── models.py       → SQLAlchemy models + business logic helper methods
 ├── routes.py       → Blueprint containing all HTTP routes + SocketIO event handlers
 └── templates/      → Jinja2 templates, one per page/view
```

**Key design decisions:**
- Business logic (e.g. `trust_score()`, `is_blocking()`, `can_comment_on()`) lives on the model classes themselves, keeping routes thin and readable
- All real-time features (messaging, typing indicators, notification badges) use Socket.IO rooms scoped per-user (`user_{id}`) rather than broadcasting globally
- AJAX endpoints are separated under `/ajax/*` to keep traditional form-based routes and JSON API routes clearly distinct
- Safety checks (block/mute/permission checks) are enforced at the route level before any database mutation, not just hidden in the UI

---

## Database Schema

Core entities and relationships:

- **User** — central entity with relationships to posts, follows, blocks, mutes, messages, notifications, stories, thoughts, and capsules
- **Post** — supports polls, images, privacy flags, and sensitive content flags
- **Follow** — self-referential many-to-many with `pending` / `accepted` status for private accounts
- **Block / Mute** — separate one-directional relationship tables enforcing safety rules
- **Report** — polymorphic reference (can target either a `User` or a `Post`)
- **Story / Thought / MemoryCapsule** — time-bound content with automatic expiry/unlock logic
- **Message** — direct messages with read-state tracking
- **Notification** — typed (`like`, `comment`, `mention`, `follow_request`, `follow_accept`, `reaction`, `capsule_unlocked`)

All destructive relationships use `cascade="all, delete"` to maintain referential integrity on account deletion.

---

## Installation

### Prerequisites
- Python 3.10+
- pip

### Setup

```bash
# Clone the repository
git clone https://github.com/SimranRiyar/lynk.git
cd lynk

# Create virtual environment
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the application
python run.py
```

The app will be available at `http://localhost:5000`. The SQLite database is created automatically on first run.

---

## Configuration

Environment-specific settings live in `config.py`:

```python
SECRET_KEY              # Flask session secret
SQLALCHEMY_DATABASE_URI # Database connection string
MAIL_SERVER             # SMTP server for transactional emails
MAIL_PORT
MAIL_USERNAME
MAIL_PASSWORD           # Use an app password, not your real password
MAIL_DEFAULT_SENDER
```

For production, all secrets should be moved to environment variables rather than hardcoded.

---

## Project Structure

```
lynk/
├── app/
│   ├── __init__.py        # Application factory
│   ├── extensions.py      # Flask extension instances
│   ├── models.py          # Database models
│   ├── routes.py          # All routes + SocketIO handlers
│   ├── forms.py           # Form definitions
│   ├── static/
│   │   ├── img/
│   │   ├── js/
│   │   │   └── ajax.js    # Reusable AJAX interaction handlers
│   │   └── uploads/       # User-uploaded media
│   └── templates/
│       ├── base.html      # Shared layout, navbar, theming
│       ├── index.html     # Main feed
│       ├── profile.html
│       ├── messages.html / conversation.html
│       ├── settings.html
│       └── ...
├── config.py
├── run.py
└── requirements.txt
```

---

## Screenshots
**Main Feed**
<img width="1359" height="609" alt="Lynk Feed" src="https://github.com/user-attachments/assets/8708515a-b246-4b6b-857a-980e2ae607c2" />
**Profile & Trust Score**
<img width="1359" height="606" alt="Lynk Profile" src="https://github.com/user-attachments/assets/fe8e6856-688e-4ffc-b1c5-6e045cd3826e" />
**Real-Time Messaging**
<img width="964" height="536" alt="Lynk Messages" src="https://github.com/user-attachments/assets/746eb91b-adcd-4837-9f6b-a4cfdc60d6a6" />
**Admin Dashboard**
<img width="1318" height="606" alt="Lynk Admin Panel" src="https://github.com/user-attachments/assets/61d2edd1-dae6-4392-85f9-07aaacb73d63" />

---

## Roadmap

This project was built in clearly defined phases, each shipped and tested independently before moving to the next:

- [x] Authentication, 2FA, account security
- [x] Posts, comments, reactions, polls
- [x] Follow system with private accounts
- [x] Stories, Thoughts, Memory Capsules
- [x] Real-time messaging (Socket.IO)
- [x] Notifications & activity feed
- [x] Explore, search, hashtags
- [x] Theming system (5 palettes × light/dark)
- [x] Safety & Trust — block, mute, report, sensitive content, trust score, comment permissions, profile views, pause account
- [x] AI Agents — chat assistant, caption/bio generation, smart replies, hashtag recommender
- [x] Admin panel — user management, moderation queue, AI flagging dashboard, stats
- [x] Polish — custom error pages, loading skeletons, animations, empty states
- [x] Production deployment — Railway, PostgreSQL, SSL

**Status: Feature-complete and deployed.**

---

## License

This project is available for portfolio and demonstration purposes. Contact the author for licensing inquiries regarding commercial use.

---

## Author

Built by **Simran Riyar** — Full-stack developer specializing in AI-powered web applications and real-time systems.
💼 Available for freelance/contract work on Upwork

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import threading
import time
import requests
from functools import wraps
import json
import os

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_premium = db.Column(db.Boolean, default=False)
    max_urls = db.Column(db.Integer, default=2)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    urls = db.relationship('URL', backref='user', lazy=True)

class URL(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False)
    interval = db.Column(db.Integer, nullable=False)  # in minutes
    last_checked = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MonitorLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url_id = db.Column(db.Integer, db.ForeignKey('url.id'), nullable=False)
    status_code = db.Column(db.Integer)
    response_time = db.Column(db.Float)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    success = db.Column(db.Boolean)

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('username') != 'admin':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Monitoring function
def monitor_url(url_obj):
    while url_obj.is_active:
        try:
            start_time = time.time()
            response = requests.get(url_obj.url, timeout=10)
            response_time = time.time() - start_time
            
            log = MonitorLog(
                url_id=url_obj.id,
                status_code=response.status_code,
                response_time=response_time,
                success=200 <= response.status_code < 400
            )
            db.session.add(log)
            url_obj.last_checked = datetime.utcnow()
            db.session.commit()
            
        except Exception as e:
            log = MonitorLog(
                url_id=url_obj.id,
                status_code=0,
                response_time=0,
                success=False
            )
            db.session.add(log)
            url_obj.last_checked = datetime.utcnow()
            db.session.commit()
        
        time.sleep(url_obj.interval * 60)

# Start monitoring for a URL
def start_monitoring(url_id):
    with app.app_context():
        url_obj = URL.query.get(url_id)
        if url_obj and url_obj.is_active:
            thread = threading.Thread(target=monitor_url, args=(url_obj,))
            thread.daemon = True
            thread.start()

# Start monitoring for all active URLs
def start_all_monitoring():
    with app.app_context():
        active_urls = URL.query.filter_by(is_active=True).all()
        for url in active_urls:
            start_monitoring(url.id)

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Admin login
        if username == 'admin' and password == 'admin@12':
            session['user_id'] = 0
            session['username'] = 'admin'
            session['is_admin'] = True
            return redirect(url_for('admin_dashboard'))
        
        # User login
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_premium'] = user.is_premium
            session['max_urls'] = user.max_urls
            return redirect(url_for('dashboard'))
        
        flash('Invalid credentials!')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists!')
            return redirect(url_for('register'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already registered!')
            return redirect(url_for('register'))
        
        user = User(
            username=username,
            email=email,
            password=password,
            is_premium=False,
            max_urls=2
        )
        db.session.add(user)
        db.session.commit()
        
        flash('Registration successful! Please login.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    user = User.query.get(user_id)
    urls = URL.query.filter_by(user_id=user_id).all()
    
    # Calculate uptime statistics
    url_stats = []
    for url in urls:
        logs = MonitorLog.query.filter_by(url_id=url.id).order_by(MonitorLog.checked_at.desc()).limit(100).all()
        if logs:
            success_count = sum(1 for log in logs if log.success)
            uptime_percentage = (success_count / len(logs)) * 100
        else:
            uptime_percentage = 0
        
        url_stats.append({
            'url': url,
            'uptime': uptime_percentage,
            'last_checked': url.last_checked
        })
    
    return render_template('dashboard.html', 
                         user=user, 
                         urls=url_stats,
                         max_urls=session['max_urls'])

@app.route('/add_url', methods=['POST'])
@login_required
def add_url():
    user_id = session['user_id']
    user = User.query.get(user_id)
    
    # Check URL limit
    current_urls = URL.query.filter_by(user_id=user_id).count()
    if current_urls >= user.max_urls:
        return jsonify({'success': False, 'message': 'URL limit reached! Upgrade to premium.'})
    
    url = request.form['url']
    interval = int(request.form['interval'])
    
    # Validate URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    new_url = URL(
        url=url,
        interval=interval,
        user_id=user_id,
        is_active=True
    )
    db.session.add(new_url)
    db.session.commit()
    
    # Start monitoring in background
    start_monitoring(new_url.id)
    
    return jsonify({'success': True})

@app.route('/delete_url/<int:url_id>', methods=['POST'])
@login_required
def delete_url(url_id):
    url = URL.query.get(url_id)
    if url and url.user_id == session['user_id']:
        url.is_active = False
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/update_interval/<int:url_id>', methods=['POST'])
@login_required
def update_interval(url_id):
    url = URL.query.get(url_id)
    if url and url.user_id == session['user_id']:
        interval = int(request.form['interval'])
        url.interval = interval
        db.session.commit()
        
        # Restart monitoring with new interval
        url.is_active = True
        db.session.commit()
        start_monitoring(url.id)
        
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/premium')
@login_required
def premium():
    return render_template('premium.html')

@app.route('/purchase_premium', methods=['POST'])
@login_required
def purchase_premium():
    # Redirect to Telegram for payment
    return jsonify({'redirect': 'https://t.me/MUNNA_TCP'})

@app.route('/admin')
@admin_required
def admin_dashboard():
    users = User.query.all()
    total_urls = URL.query.count()
    active_urls = URL.query.filter_by(is_active=True).count()
    
    return render_template('admin.html', 
                         users=users,
                         total_urls=total_urls,
                         active_urls=active_urls)

@app.route('/admin/update_user', methods=['POST'])
@admin_required
def update_user():
    user_id = int(request.form['user_id'])
    is_premium = request.form.get('is_premium') == 'true'
    max_urls = int(request.form['max_urls'])
    
    user = User.query.get(user_id)
    if user:
        user.is_premium = is_premium
        user.max_urls = max_urls
        db.session.commit()
        
        # Update session if user is viewing their own dashboard
        if session.get('user_id') == user_id:
            session['is_premium'] = is_premium
            session['max_urls'] = max_urls
            
        return jsonify({'success': True})
    
    return jsonify({'success': False})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# Initialize database and start monitoring
with app.app_context():
    db.create_all()
    
    # Create admin user if not exists
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            email='admin@uptimemonitor.com',
            password='admin@12',
            is_premium=True,
            max_urls=100
        )
        db.session.add(admin)
        db.session.commit()
    
    # Start monitoring for all active URLs
    start_all_monitoring()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
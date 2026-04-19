from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room, leave_room
from functools import wraps
import eventlet

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'skillswapsrmu-secret-key-change-in-production')

DATABASE = 'database.db'

# Initialize SocketIO with eventlet for production
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Chat rate limiting storage
message_timestamps = {}  # user_id -> list of timestamps

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            credits INTEGER DEFAULT 10,
            rating REAL DEFAULT 0.0,
            total_ratings INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Skills table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill_name TEXT NOT NULL,
            skill_type TEXT NOT NULL, -- 'have' or 'want'
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    # Matches table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            match_score REAL NOT NULL,
            status TEXT DEFAULT 'pending', -- 'pending', 'connected', 'completed'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user1_id) REFERENCES users (id),
            FOREIGN KEY (user2_id) REFERENCES users (id)
        )
    ''')
    
    # Sessions/Transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            learner_id INTEGER NOT NULL,
            skill_taught TEXT NOT NULL,
            credits_exchanged INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active', -- 'active', 'completed'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (teacher_id) REFERENCES users (id),
            FOREIGN KEY (learner_id) REFERENCES users (id)
        )
    ''')

    # Chat Rooms table - stores chat sessions between matched users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT UNIQUE NOT NULL,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMP,
            FOREIGN KEY (user1_id) REFERENCES users (id),
            FOREIGN KEY (user2_id) REFERENCES users (id),
            UNIQUE(user1_id, user2_id)
        )
    ''')

    # Chat Messages table - stores all messages
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            is_read BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES chat_rooms (room_id),
            FOREIGN KEY (sender_id) REFERENCES users (id),
            FOREIGN KEY (receiver_id) REFERENCES users (id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# ==================== AUTHENTICATION ROUTES ====================

@app.route('/')
def landing():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return render_template('landing.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        skills_have = request.form.getlist('skills_have')
        skills_want = request.form.getlist('skills_want')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if email exists
        cursor.execute('SELECT id FROM users WHERE email = ?', (email,))
        if cursor.fetchone():
            flash('Email already registered!', 'error')
            conn.close()
            return redirect(url_for('signup'))
        
        # Hash password and create user
        hashed_password = generate_password_hash(password)
        cursor.execute('''
            INSERT INTO users (name, email, password) 
            VALUES (?, ?, ?)
        ''', (name, email, hashed_password))
        
        user_id = cursor.lastrowid
        
        # Add skills
        for skill in skills_have:
            if skill.strip():
                cursor.execute('''
                    INSERT INTO skills (user_id, skill_name, skill_type) 
                    VALUES (?, ?, 'have')
                ''', (user_id, skill.strip().lower()))
        
        for skill in skills_want:
            if skill.strip():
                cursor.execute('''
                    INSERT INTO skills (user_id, skill_name, skill_type) 
                    VALUES (?, ?, 'want')
                ''', (user_id, skill.strip().lower()))
        
        conn.commit()
        conn.close()
        
        flash('Account created successfully! Please login.', 'success')
        return redirect(url_for('login'))
    
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            flash(f'Welcome back, {user["name"]}!', 'success')
            return redirect(url_for('home'))
        else:
            flash('Invalid email or password!', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('landing'))

# ==================== MAIN APP ROUTES ====================

@app.route('/home')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get current user
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    
    # Get user's skills
    cursor.execute('SELECT * FROM skills WHERE user_id = ?', (session['user_id'],))
    user_skills = cursor.fetchall()
    
    # Get popular skills
    cursor.execute('''
        SELECT skill_name, COUNT(*) as count 
        FROM skills 
        GROUP BY skill_name 
        ORDER BY count DESC 
        LIMIT 5
    ''')
    popular_skills = cursor.fetchall()
    
    # Get top matches
    matches = get_matches_for_user(session['user_id'], cursor)
    
    conn.close()
    
    return render_template('home.html', 
                         user=user, 
                         user_skills=user_skills,
                         popular_skills=popular_skills,
                         matches=matches[:4])

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    
    cursor.execute('SELECT * FROM skills WHERE user_id = ?', (session['user_id'],))
    skills = cursor.fetchall()
    
    skills_have = [s for s in skills if s['skill_type'] == 'have']
    skills_want = [s for s in skills if s['skill_type'] == 'want']
    
    conn.close()
    
    return render_template('profile.html', 
                         user=user, 
                         skills_have=skills_have, 
                         skills_want=skills_want)

@app.route('/matches')
def matches():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    matches = get_matches_for_user(session['user_id'], cursor)
    
    conn.close()
    
    return render_template('matches.html', matches=matches)

@app.route('/wallet')
def wallet():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    
    # Get transaction history
    cursor.execute('''
        SELECT s.*, 
               u1.name as teacher_name, 
               u2.name as learner_name 
        FROM sessions s
        JOIN users u1 ON s.teacher_id = u1.id
        JOIN users u2 ON s.learner_id = u2.id
        WHERE s.teacher_id = ? OR s.learner_id = ?
        ORDER BY s.created_at DESC
    ''', (session['user_id'], session['user_id']))
    transactions = cursor.fetchall()
    
    conn.close()
    
    return render_template('wallet.html', user=user, transactions=transactions)

# ==================== API ROUTES ====================

@app.route('/api/add-skill', methods=['POST'])
def add_skill():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.get_json()
    skill_name = data.get('skill_name', '').strip().lower()
    skill_type = data.get('skill_type', 'have')
    
    if not skill_name:
        return jsonify({'error': 'Skill name is required'}), 400
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO skills (user_id, skill_name, skill_type) 
        VALUES (?, ?, ?)
    ''', (session['user_id'], skill_name, skill_type))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Skill added successfully'})

@app.route('/api/remove-skill/<int:skill_id>', methods=['DELETE'])
def remove_skill(skill_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        DELETE FROM skills 
        WHERE id = ? AND user_id = ?
    ''', (skill_id, session['user_id']))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Skill removed successfully'})

@app.route('/api/connect/<int:match_user_id>', methods=['POST'])
def connect_user(match_user_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if match already exists
    cursor.execute('''
        SELECT id FROM matches 
        WHERE (user1_id = ? AND user2_id = ?) 
           OR (user1_id = ? AND user2_id = ?)
    ''', (session['user_id'], match_user_id, match_user_id, session['user_id']))
    
    existing = cursor.fetchone()
    
    if existing:
        cursor.execute('''
            UPDATE matches SET status = 'connected' WHERE id = ?
        ''', (existing['id'],))
    else:
        cursor.execute('''
            INSERT INTO matches (user1_id, user2_id, match_score, status) 
            VALUES (?, ?, 0, 'connected')
        ''', (session['user_id'], match_user_id))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Connected successfully!'})

@app.route('/api/search-users')
def search_users():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    query = request.args.get('q', '').lower()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT u.id, u.name, u.rating 
        FROM users u
        JOIN skills s ON u.id = s.user_id
        WHERE s.skill_name LIKE ? AND u.id != ?
    ''', (f'%{query}%', session['user_id']))
    
    users = cursor.fetchall()
    
    result = []
    for user in users:
        cursor.execute('''
            SELECT skill_name, skill_type FROM skills WHERE user_id = ?
        ''', (user['id'],))
        skills = cursor.fetchall()
        
        result.append({
            'id': user['id'],
            'name': user['name'],
            'rating': user['rating'],
            'skills': [{'name': s['skill_name'], 'type': s['skill_type']} for s in skills]
        })
    
    conn.close()
    
    return jsonify({'users': result})

# ==================== AI MATCHING SYSTEM ====================

def get_matches_for_user(user_id, cursor):
    """AI Matching Algorithm - Find compatible skill partners"""
    
    # Get current user's skills
    cursor.execute('SELECT * FROM skills WHERE user_id = ?', (user_id,))
    user_skills = cursor.fetchall()
    
    user_have = [s['skill_name'] for s in user_skills if s['skill_type'] == 'have']
    user_want = [s['skill_name'] for s in user_skills if s['skill_type'] == 'want']
    
    if not user_have and not user_want:
        return []
    
    # Get all other users with their skills
    cursor.execute('''
        SELECT u.id, u.name, u.rating, u.credits,
               s.skill_name, s.skill_type
        FROM users u
        LEFT JOIN skills s ON u.id = s.user_id
        WHERE u.id != ?
    ''', (user_id,))
    
    all_data = cursor.fetchall()
    
    # Group by user
    users_data = {}
    for row in all_data:
        uid = row['id']
        if uid not in users_data:
            users_data[uid] = {
                'id': uid,
                'name': row['name'],
                'rating': row['rating'],
                'credits': row['credits'],
                'have': [],
                'want': []
            }
        if row['skill_name']:
            if row['skill_type'] == 'have':
                users_data[uid]['have'].append(row['skill_name'])
            else:
                users_data[uid]['want'].append(row['skill_name'])
    
    # Calculate match scores
    matches = []
    for uid, data in users_data.items():
        score = 0
        match_reasons = []
        
        # Score: user_have matches other.want (can teach them)
        for skill in user_have:
            if skill in data['want']:
                score += 50
                match_reasons.append(f"You can teach {skill}")
        
        # Score: user_want matches other.have (can learn from them)
        for skill in user_want:
            if skill in data['have']:
                score += 50
                match_reasons.append(f"They can teach {skill}")
        
        # Mutual interest bonus
        mutual_skills = set(user_have) & set(data['have'])
        if mutual_skills:
            score += 10
        
        # Rating bonus (0-10 points based on rating)
        score += min(data['rating'] * 2, 10)
        
        if score > 0:
            matches.append({
                'user': data,
                'score': min(score, 100),
                'reasons': match_reasons[:3]  # Top 3 reasons
            })
    
    # Sort by score (highest first)
    matches.sort(key=lambda x: x['score'], reverse=True)
    
    return matches

# ==================== CHAT SYSTEM ====================

def get_or_create_room(user1_id, user2_id):
    """Get existing chat room or create new one between two users"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure user1_id is always the smaller ID for consistency
    if user1_id > user2_id:
        user1_id, user2_id = user2_id, user1_id
    
    # Check if room exists
    cursor.execute('''
        SELECT room_id FROM chat_rooms 
        WHERE user1_id = ? AND user2_id = ?
    ''', (user1_id, user2_id))
    
    result = cursor.fetchone()
    
    if result:
        room_id = result['room_id']
    else:
        # Create new room with unique ID
        room_id = f"room_{user1_id}_{user2_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        cursor.execute('''
            INSERT INTO chat_rooms (room_id, user1_id, user2_id)
            VALUES (?, ?, ?)
        ''', (room_id, user1_id, user2_id))
        conn.commit()
    
    conn.close()
    return room_id

def can_users_chat(user1_id, user2_id):
    """Check if two users are matched (connected) and can chat"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if they have a connected match
    cursor.execute('''
        SELECT id FROM matches 
        WHERE ((user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?))
        AND status = 'connected'
    ''', (user1_id, user2_id, user2_id, user1_id))
    
    result = cursor.fetchone()
    conn.close()
    
    return result is not None

def get_chat_history(room_id, limit=50):
    """Get chat history for a room"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT cm.*, u.name as sender_name
        FROM chat_messages cm
        JOIN users u ON cm.sender_id = u.id
        WHERE cm.room_id = ?
        ORDER BY cm.created_at DESC
        LIMIT ?
    ''', (room_id, limit))
    
    messages = cursor.fetchall()
    conn.close()
    
    return list(reversed(messages))  # Return in chronological order

def get_user_chat_list(user_id):
    """Get list of all chat conversations for a user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            cr.room_id,
            CASE 
                WHEN cr.user1_id = ? THEN cr.user2_id
                ELSE cr.user1_id
            END as other_user_id,
            u.name as other_user_name,
            cr.last_message_at,
            (SELECT COUNT(*) FROM chat_messages 
             WHERE room_id = cr.room_id AND receiver_id = ? AND is_read = 0) as unread_count
        FROM chat_rooms cr
        JOIN users u ON u.id = CASE 
                WHEN cr.user1_id = ? THEN cr.user2_id
                ELSE cr.user1_id
            END
        WHERE cr.user1_id = ? OR cr.user2_id = ?
        ORDER BY cr.last_message_at DESC
    ''', (user_id, user_id, user_id, user_id, user_id))
    
    chats = cursor.fetchall()
    conn.close()
    
    return chats

def check_rate_limit(user_id, max_messages=20, time_window=60):
    """Check if user is sending messages too fast (rate limiting)"""
    global message_timestamps
    
    now = datetime.now().timestamp()
    
    # Get user's message history
    if user_id not in message_timestamps:
        message_timestamps[user_id] = []
    
    # Remove old timestamps outside the time window
    message_timestamps[user_id] = [
        ts for ts in message_timestamps[user_id]
        if now - ts < time_window
    ]
    
    # Check if limit exceeded
    if len(message_timestamps[user_id]) >= max_messages:
        return False
    
    # Add new timestamp
    message_timestamps[user_id].append(now)
    return True

# Chat Routes

@app.route('/chat')
def chat_list():
    """Show list of all chat conversations"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    chats = get_user_chat_list(session['user_id'])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get user info
    cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],))
    user = cursor.fetchone()
    conn.close()
    
    return render_template('chat_list.html', chats=chats, user=user)

@app.route('/chat/<int:other_user_id>')
def chat_room(other_user_id):
    """Show chat room with specific user"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    
    # Prevent chatting with yourself
    if user_id == other_user_id:
        flash('You cannot chat with yourself!', 'error')
        return redirect(url_for('chat_list'))
    
    # Check if users are matched
    if not can_users_chat(user_id, other_user_id):
        flash('You can only chat with matched users. Please connect with this user first!', 'error')
        return redirect(url_for('matches'))
    
    # Get or create room
    room_id = get_or_create_room(user_id, other_user_id)
    
    # Get chat history
    messages = get_chat_history(room_id)
    
    # Get other user info
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, email FROM users WHERE id = ?', (other_user_id,))
    other_user = cursor.fetchone()
    
    # Mark messages as read
    cursor.execute('''
        UPDATE chat_messages SET is_read = 1
        WHERE room_id = ? AND receiver_id = ? AND is_read = 0
    ''', (room_id, user_id))
    conn.commit()
    conn.close()
    
    return render_template('chat.html', 
                         room_id=room_id, 
                         messages=messages, 
                         other_user=other_user,
                         user_id=user_id)

# SocketIO Event Handlers for Real-time Chat

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    if 'user_id' not in session:
        return False  # Reject connection if not authenticated
    
    print(f"User {session['user_id']} connected to SocketIO")
    emit('connected', {'message': 'Connected to chat server'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    if 'user_id' in session:
        print(f"User {session['user_id']} disconnected from SocketIO")

@socketio.on('join')
def handle_join(data):
    """Handle user joining a chat room"""
    room_id = data.get('room_id')
    user_id = session.get('user_id')
    
    if not room_id or not user_id:
        return
    
    # Verify user is part of this room
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user1_id, user2_id FROM chat_rooms WHERE room_id = ?
    ''', (room_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and (result['user1_id'] == user_id or result['user2_id'] == user_id):
        join_room(room_id)
        emit('joined', {'room_id': room_id, 'message': 'Joined room successfully'})
        print(f"User {user_id} joined room {room_id}")
    else:
        emit('error', {'message': 'Not authorized to join this room'})

@socketio.on('leave')
def handle_leave(data):
    """Handle user leaving a chat room"""
    room_id = data.get('room_id')
    leave_room(room_id)
    emit('left', {'room_id': room_id})

@socketio.on('send_message')
def handle_send_message(data):
    """Handle sending a message in real-time"""
    user_id = session.get('user_id')
    
    if not user_id:
        emit('error', {'message': 'Not authenticated'})
        return
    
    room_id = data.get('room_id')
    message = data.get('message', '').strip()
    receiver_id = data.get('receiver_id')
    
    # Validate inputs
    if not room_id or not message or not receiver_id:
        emit('error', {'message': 'Missing required fields'})
        return
    
    # Check rate limiting (20 messages per minute)
    if not check_rate_limit(user_id):
        emit('error', {'message': 'Too many messages. Please slow down.'})
        return
    
    # Check message length
    if len(message) > 2000:
        emit('error', {'message': 'Message too long (max 2000 characters)'})
        return
    
    # Verify users can chat
    if not can_users_chat(user_id, receiver_id):
        emit('error', {'message': 'You are not connected with this user'})
        return
    
    # Save message to database
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO chat_messages (room_id, sender_id, receiver_id, message)
            VALUES (?, ?, ?, ?)
        ''', (room_id, user_id, receiver_id, message))
        
        # Update last message timestamp
        cursor.execute('''
            UPDATE chat_rooms SET last_message_at = CURRENT_TIMESTAMP
            WHERE room_id = ?
        ''', (room_id,))
        
        conn.commit()
        
        # Get sender name
        cursor.execute('SELECT name FROM users WHERE id = ?', (user_id,))
        sender = cursor.fetchone()
        sender_name = sender['name'] if sender else 'Unknown'
        
        message_id = cursor.lastrowid
        
    except Exception as e:
        conn.close()
        emit('error', {'message': 'Failed to send message'})
        return
    
    conn.close()
    
    # Broadcast message to room
    message_data = {
        'id': message_id,
        'room_id': room_id,
        'sender_id': user_id,
        'sender_name': sender_name,
        'message': message,
        'created_at': datetime.now().isoformat()
    }
    
    # Emit to all clients in the room (including sender)
    emit('new_message', message_data, room=room_id)
    
    print(f"Message sent in room {room_id} by user {user_id}")

@socketio.on('typing')
def handle_typing(data):
    """Handle typing indicator"""
    room_id = data.get('room_id')
    user_id = session.get('user_id')
    is_typing = data.get('is_typing', False)
    
    if room_id and user_id:
        emit('user_typing', {
            'user_id': user_id,
            'is_typing': is_typing
        }, room=room_id, include_self=False)

@socketio.on('mark_read')
def handle_mark_read(data):
    """Handle marking messages as read"""
    room_id = data.get('room_id')
    user_id = session.get('user_id')
    
    if not room_id or not user_id:
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE chat_messages SET is_read = 1
        WHERE room_id = ? AND receiver_id = ? AND is_read = 0
    ''', (room_id, user_id))
    conn.commit()
    conn.close()
    
    emit('messages_read', {'room_id': room_id})

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

if _name_ == '_main_':
    # Get the port from Render's environment, default to 10000 if not found
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, debug=True, host='0.0.0.0', port=port)

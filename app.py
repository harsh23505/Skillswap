from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'skillswapsrmu-secret-key-change-in-production'
app.config['SERVER_NAME'] = 'skillswap.com:5000'

DATABASE = 'database.db'

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

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

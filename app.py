from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import os
import random
import json
import secrets
from datetime import datetime
import time
import threading
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import certifi
import ssl

try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

app = Flask(__name__, static_folder='public', static_url_path='')
app.secret_key = secrets.token_hex(16)

# MongoDB connection with improved SSL configuration
mongo_uri = "mongodb+srv://x3doge:OMGbbpro2010@403cluster0.zkvn1jo.mongodb.net/?retryWrites=true&w=majority&appName=403Cluster0"

# Initialize database connections
db = None
users_collection = None
games_collection = None
in_memory_users = None
in_memory_games = None

# Simple MongoDB connection function with minimal options
def connect_to_mongodb():
    global db, users_collection, games_collection, in_memory_users, in_memory_games
    
    # Connection options to try - minimal parameters to avoid errors
    try:
        print("Attempting MongoDB connection with default settings")
        # Just use the URI with certifi for TLS certificate validation
        client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
        
        # Test connection
        client.admin.command('ping')
        print("MongoDB connection successful")
        
        db = client["casino_db"]
        users_collection = db["users"]
        games_collection = db["games"]
        return True
    except Exception as e:
        print(f"MongoDB connection failed: {e}")
        # Try a second attempt with tlsAllowInvalidCertificates=True
        try:
            print("Attempting MongoDB connection with relaxed TLS settings")
            client = MongoClient(mongo_uri, tlsAllowInvalidCertificates=True)
            client.admin.command('ping')
            print("MongoDB connection successful with relaxed TLS settings")
            
            db = client["casino_db"]
            users_collection = db["users"]
            games_collection = db["games"]
            return True
        except Exception as e:
            print(f"MongoDB connection with relaxed TLS failed: {e}")
            
            # Try a third attempt with no TLS
            try:
                print("Attempting MongoDB connection with no TLS")
                client = MongoClient(mongo_uri, tls=False)
                client.admin.command('ping')
                print("MongoDB connection successful with no TLS")
                
                db = client["casino_db"]
                users_collection = db["users"]
                games_collection = db["games"]
                return True
            except Exception as e:
                print(f"All MongoDB connection attempts failed: {e}")
                # Fall back to in-memory storage
                setup_in_memory_storage()
                return False

def setup_in_memory_storage():
    global in_memory_users, in_memory_games
    print("Setting up in-memory data storage")
    # Initialize with default users
    in_memory_users = [
        {"username": "admin", "password": "admin123", "balance": 100},
        {"username": "user1", "password": "password123", "balance": 5000},
        {"username": "user2", "password": "password123", "balance": 7500}
    ]
    in_memory_games = []
    print(f"In-memory storage initialized with {len(in_memory_users)} users")

# Try to connect to MongoDB
connect_to_mongodb()

# Shared crash game state
crash_game_state = {
    'status': 'waiting',  # waiting, active, crashed
    'current_multiplier': 1.0,
    'crash_point': 0,
    'start_time': 0,
    'active_players': {},  # username -> {bet, auto_cashout}
    'players_online': set(),  # Set of usernames who are currently online
    'game_history': [],  # List of past crash points
    'waiting_players': {}  # Players waiting for next round
}

crash_game_lock = threading.Lock()  # Lock for thread-safe access to crash_game_state

# Initialize data if the database is empty
def initialize_database():
    # Skip if we're using in-memory storage
    if users_collection is None:
        return
        
    try:
        if users_collection.count_documents({}) == 0:
            default_users = [
                {"username": "admin", "password": "admin123", "balance": 100},
                {"username": "user1", "password": "password123", "balance": 5000},
                {"username": "user2", "password": "password123", "balance": 7500}
            ]
            users_collection.insert_many(default_users)
            print("Initialized database with default users")
    except Exception as e:
        print(f"Error initializing database: {e}")
        # If initialization fails, switch to in-memory storage
        setup_in_memory_storage()

# Call initialize database at startup
initialize_database()

# Helper function to get user data (works with both MongoDB and fallback)
def get_user(username):
    if users_collection is not None:
        try:
            return users_collection.find_one({"username": username})
        except Exception as e:
            print(f"Error getting user from MongoDB: {e}")
            # If MongoDB query fails, try the in-memory data if available
            if in_memory_users:
                for user in in_memory_users:
                    if user["username"] == username:
                        return user
    else:
        # Fallback to in-memory
        for user in in_memory_users:
            if user["username"] == username:
                return user
    return None

# Helper function to update user balance (works with both MongoDB and fallback)
def update_user_balance(username, amount, is_set=False):
    if users_collection is not None:
        try:
            if is_set:
                return users_collection.update_one(
                    {"username": username},
                    {"$set": {"balance": amount}}
                )
            else:
                return users_collection.update_one(
                    {"username": username},
                    {"$inc": {"balance": amount}}
                )
        except Exception as e:
            print(f"Error updating balance in MongoDB: {e}")
            # If MongoDB update fails, try the in-memory data if available
            if in_memory_users:
                for user in in_memory_users:
                    if user["username"] == username:
                        if is_set:
                            user["balance"] = amount
                        else:
                            user["balance"] += amount
                        return True
    else:
        # Fallback to in-memory
        for user in in_memory_users:
            if user["username"] == username:
                if is_set:
                    user["balance"] = amount
                else:
                    user["balance"] += amount
                return True
    return False

# Helper function to save game data (works with both MongoDB and fallback)
def save_game(game_data):
    if games_collection is not None:
        try:
            return games_collection.insert_one(game_data)
        except Exception as e:
            print(f"Error saving game data to MongoDB: {e}")
            # If MongoDB insert fails, use in-memory fallback if available
            if in_memory_games is not None:
                in_memory_games.append(game_data)
                return True
    else:
        # Fallback to in-memory
        in_memory_games.append(game_data)
        return True
    return False

# Routes
@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('casino'))
    return app.send_static_file('index.html')

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = get_user(username)
    
    if user and user["password"] == password:
        session['username'] = username
        return jsonify({'success': True, 'balance': user["balance"]})
    
    return jsonify({'success': False, 'message': 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง'})

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    existing_user = get_user(username)
    if existing_user:
        return jsonify({'success': False, 'message': 'ชื่อผู้ใช้นี้มีอยู่แล้ว'})
    
    # Create new user
    new_user = {
        "username": username,
        "password": password,
        "balance": 100,  # Starting balance 100 baht
        "created_at": datetime.now()
    }
    
    update_user_balance(username, 100)
    session['username'] = username
    
    return jsonify({'success': True, 'balance': new_user["balance"]})

@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        # Remove from online players
        with crash_game_lock:
            if username in crash_game_state['players_online']:
                crash_game_state['players_online'].remove(username)
    
    session.pop('username', None)
    return redirect(url_for('index'))

@app.route('/reset_balance', methods=['POST'])
def reset_balance():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    username = session['username']
    # Reset balance to 100 baht
    update_user_balance(username, 100, True)
    
    return jsonify({'success': True, 'message': 'รีเซ็ตยอดเงินเป็น 100 บาทเรียบร้อยแล้ว', 'balance': 100})

@app.route('/casino')
def casino():
    if 'username' not in session:
        return redirect(url_for('index'))
    return app.send_static_file('casino.html')

@app.route('/blackjack')
def blackjack():
    if 'username' not in session:
        return redirect(url_for('index'))
    return app.send_static_file('blackjack.html')

@app.route('/slot')
def slot():
    if 'username' not in session:
        return redirect(url_for('index'))
    return app.send_static_file('slot.html')

@app.route('/crash')
def crash():
    if 'username' not in session:
        return redirect(url_for('index'))
    
    # Add user to online players
    username = session['username']
    with crash_game_lock:
        crash_game_state['players_online'].add(username)
    
    return app.send_static_file('crash.html')

@app.route('/plinko')
def plinko():
    if 'username' not in session:
        return redirect(url_for('index'))
    return app.send_static_file('dropball.html')

@app.route('/get_balance')
def get_balance():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    username = session['username']
    user = get_user(username)
    
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบผู้ใช้'})
    
    return jsonify({
        'success': True, 
        'balance': user["balance"],
        'username': username
    })

# Blackjack Game Logic
def create_deck():
    suits = ['hearts', 'diamonds', 'clubs', 'spades']
    values = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    deck = [{'suit': suit, 'value': value} for suit in suits for value in values]
    random.shuffle(deck)
    return deck

def calculate_hand_value(hand):
    value = 0
    aces = 0
    
    for card in hand:
        if card['value'] in ['J', 'Q', 'K']:
            value += 10
        elif card['value'] == 'A':
            aces += 1
            value += 11
        else:
            value += int(card['value'])
    
    # Adjust for aces
    while value > 21 and aces > 0:
        value -= 10
        aces -= 1
    
    return value

@app.route('/blackjack/start', methods=['POST'])
def start_blackjack():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    data = request.get_json()
    bet = data.get('bet', 0)
    username = session['username']
    
    # Get user from MongoDB
    user = get_user(username)
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบผู้ใช้'})
    
    if bet <= 0 or bet > user["balance"]:
        return jsonify({'success': False, 'message': 'จำนวนเงินเดิมพันไม่ถูกต้อง'})
    
    # Deduct bet amount
    update_user_balance(username, -bet)
    
    # Create new game
    deck = create_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    
    game_id = secrets.token_hex(8)
    game_data = {
        'game_id': game_id,
        'game_type': 'blackjack',
        'username': username,
        'bet': bet,
        'deck': deck,
        'player_hand': player_hand,
        'dealer_hand': dealer_hand,
        'status': 'playing',
        'timestamp': datetime.now()
    }
    
    save_game(game_data)
    
    player_value = calculate_hand_value(player_hand)
    dealer_value = calculate_hand_value([dealer_hand[0]])  # Only show first card
    
    # Check for natural blackjack
    if player_value == 21:
        dealer_value = calculate_hand_value(dealer_hand)
        if dealer_value == 21:
            # Push - both have blackjack
            update_user_balance(username, bet)
            game_data['status'] = "push"
        else:
            # Player wins with blackjack (pays 3:2)
            win_amount = bet + (bet * 1.5)
            update_user_balance(username, win_amount)
            game_data['status'] = "win"
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'game_id': game_id,
        'player_hand': player_hand,
        'dealer_hand': [dealer_hand[0], {'suit': 'hidden', 'value': 'hidden'}],
        'player_value': player_value,
        'dealer_value': dealer_value,
        'status': game_data['status'],
        'balance': user["balance"]
    })

@app.route('/blackjack/hit', methods=['POST'])
def blackjack_hit():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    data = request.get_json()
    game_id = data.get('game_id')
    username = session['username']
    
    game = get_user(username)
    if not game:
        return jsonify({'success': False, 'message': 'เกมไม่ถูกต้อง'})
    
    if game['status'] != 'playing':
        return jsonify({'success': False, 'message': 'เกมจบแล้ว'})
    
    # Deal a card to player
    deck = game['deck']
    player_hand = game['player_hand']
    player_hand.append(deck.pop())
    player_value = calculate_hand_value(player_hand)
    
    # Check if player busted
    game_status = game['status']
    if player_value > 21:
        game_status = 'lose'
    
    # Update game in database
    update_user_balance(username, -game['bet'])
    game['player_hand'] = player_hand
    game['deck'] = deck
    game['status'] = game_status
    update_user_balance(username, game['bet'])
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'player_hand': player_hand,
        'player_value': player_value,
        'status': game_status,
        'balance': user["balance"]
    })

@app.route('/blackjack/stand', methods=['POST'])
def blackjack_stand():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    data = request.get_json()
    game_id = data.get('game_id')
    username = session['username']
    
    game = get_user(username)
    if not game:
        return jsonify({'success': False, 'message': 'เกมไม่ถูกต้อง'})
    
    if game['status'] != 'playing':
        return jsonify({'success': False, 'message': 'เกมจบแล้ว'})
    
    # Dealer plays
    deck = game['deck']
    dealer_hand = game['dealer_hand']
    dealer_value = calculate_hand_value(dealer_hand)
    
    # Dealer hits until 17 or greater
    while dealer_value < 17:
        dealer_hand.append(deck.pop())
        dealer_value = calculate_hand_value(dealer_hand)
    
    player_value = calculate_hand_value(game['player_hand'])
    bet = game['bet']
    
    # Determine winner
    if dealer_value > 21:
        game_status = 'win'
        win_amount = bet * 2
        update_user_balance(username, win_amount)
    elif dealer_value > player_value:
        game_status = 'lose'
    elif dealer_value < player_value:
        game_status = 'win'
        win_amount = bet * 2
        update_user_balance(username, win_amount)
    else:
        game_status = 'push'
        update_user_balance(username, bet)
    
    # Update game in database
    update_user_balance(username, -bet)
    game['dealer_hand'] = dealer_hand
    game['deck'] = deck
    game['status'] = game_status
    update_user_balance(username, bet)
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'dealer_hand': dealer_hand,
        'dealer_value': dealer_value,
        'status': game_status,
        'balance': user["balance"]
    })

# Slot Machine Logic
@app.route('/slot/spin', methods=['POST'])
def slot_spin():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    data = request.get_json()
    bet = data.get('bet', 0)
    username = session['username']
    
    # Get user from MongoDB
    user = get_user(username)
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบผู้ใช้'})
    
    if bet <= 0 or bet > user["balance"]:
        return jsonify({'success': False, 'message': 'จำนวนเงินเดิมพันไม่ถูกต้อง'})
    
    # Deduct bet amount
    update_user_balance(username, -bet)
    
    # 5x5 grid symbols
    symbols = ['🍒', '🍊', '🍋', '🍇', '🍉', '🍓', '💎', '7️⃣', '🎰']
    weights = [20, 20, 20, 15, 15, 10, 5, 3, 2]  # Probability weights
    
    # Generate 5x5 grid
    grid = []
    for i in range(5):
        row = []
        for j in range(5):
            row.append(random.choices(symbols, weights=weights)[0])
        grid.append(row)
    
    # Check matches
    matches = check_matches(grid)
    win_amount = calculate_slot_win(matches, bet)
    
    # Create game record
    game_id = secrets.token_hex(8)
    game_data = {
        'game_id': game_id,
        'game_type': 'slot',
        'username': username,
        'bet': bet,
        'grid': grid,
        'matches': matches,
        'win_amount': win_amount,
        'timestamp': datetime.now()
    }
    save_game(game_data)
    
    if win_amount > 0:
        update_user_balance(username, win_amount)
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'grid': grid,
        'matches': matches,
        'win_amount': win_amount,
        'balance': user["balance"]
    })

def check_matches(grid):
    matches = []
    matched_positions = set()
    
    # Check horizontal, vertical, and diagonal matches of 4+ same symbols
    directions = [
        [(0, 1), (0, 2), (0, 3), (0, 4)],  # Horizontal
        [(1, 0), (2, 0), (3, 0), (4, 0)],  # Vertical
        [(1, 1), (2, 2), (3, 3), (4, 4)],  # Diagonal \
        [(1, -1), (2, -2), (3, -3), (4, -4)]  # Diagonal /
    ]
    
    for i in range(5):
        for j in range(5):
            for direction in directions:
                match_positions = [(i, j)]
                symbol = grid[i][j]
                valid = True
                
                for di, dj in direction:
                    ni, nj = i + di, j + dj
                    if 0 <= ni < 5 and 0 <= nj < 5 and grid[ni][nj] == symbol:
                        match_positions.append((ni, nj))
                    else:
                        valid = False
                        break
                
                if valid and len(match_positions) >= 4:
                    for pos in match_positions:
                        matched_positions.add(pos)
    
    for pos in matched_positions:
        i, j = pos
        matches.append({'row': i, 'col': j, 'symbol': grid[i][j]})
    
    return matches

def calculate_slot_win(matches, bet):
    if not matches:
        return 0
    
    # Count symbols
    symbol_counts = {}
    for match in matches:
        symbol = match['symbol']
        if symbol in symbol_counts:
            symbol_counts[symbol] += 1
        else:
            symbol_counts[symbol] = 1
    
    # Calculate win based on symbols and counts
    win_multiplier = 0
    for symbol, count in symbol_counts.items():
        # Base multiplier based on symbol rarity
        if symbol == '🎰':
            base_multiplier = 10
        elif symbol == '7️⃣':
            base_multiplier = 7
        elif symbol == '💎':
            base_multiplier = 5
        elif symbol in ['🍓', '🍉']:
            base_multiplier = 3
        else:
            base_multiplier = 2
        
        # Multiplier increases with more matches
        count_multiplier = count // 4  # For each set of 4
        win_multiplier += base_multiplier * (1 + count_multiplier * 0.5)
    
    return int(bet * win_multiplier)

# Crash Game Logic - Multiplayer version
def start_new_crash_game():
    with crash_game_lock:
        # Determine crash point (rigged slightly in house favor)
        crash_point = 1.0
        rand = random.random()
        if rand < 0.7:  # 70% chance of early crash
            crash_point = 1.0 + random.random() * 2.0
        else:  # 30% chance of higher multiplier
            crash_point = 3.0 + random.random() * 7.0
        
        crash_point = round(crash_point, 2)
        
        # Move waiting players to active
        crash_game_state['active_players'] = crash_game_state['waiting_players'].copy()
        crash_game_state['waiting_players'] = {}
        
        # Only start the game if there's at least one active player
        if crash_game_state['active_players']:
            # Start game
            crash_game_state['status'] = 'active'
            crash_game_state['current_multiplier'] = 1.0
            crash_game_state['crash_point'] = crash_point
            crash_game_state['start_time'] = time.time()
        else:
            # No active players, keep status as waiting
            crash_game_state['status'] = 'waiting'

def check_crash_game_status():
    """Check if game has crashed or needs to be started"""
    with crash_game_lock:
        if crash_game_state['status'] == 'waiting':
            # If players are waiting, start a new game
            if crash_game_state['waiting_players']:
                start_new_crash_game()
            return
        
        if crash_game_state['status'] != 'active':
            # Game not active, nothing to check
            return
        
        # Calculate current multiplier
        current_time = time.time()
        elapsed = current_time - crash_game_state['start_time']
        
        # Simplified calculation
        current_multiplier = 1.0 + (elapsed * 0.5)
        current_multiplier = min(current_multiplier, crash_game_state['crash_point'])
        current_multiplier = round(current_multiplier, 2)
        
        crash_game_state['current_multiplier'] = current_multiplier
        
        # Check for auto-cashouts
        for username, player_data in list(crash_game_state['active_players'].items()):
            if current_multiplier >= player_data.get('auto_cashout', float('inf')):
                # Auto cashout
                bet = player_data['bet']
                win_amount = int(bet * current_multiplier)
                
                # Update user balance in MongoDB
                update_user_balance(username, win_amount)
                
                # Record game in history
                game_data = {
                    'game_id': secrets.token_hex(8),
                    'game_type': 'crash',
                    'username': username,
                    'bet': bet,
                    'cashout_multiplier': current_multiplier,
                    'win_amount': win_amount,
                    'auto_cashout': True,
                    'timestamp': datetime.now()
                }
                save_game(game_data)
                
                # Remove from active players
                del crash_game_state['active_players'][username]
        
        # Check for crash
        if current_multiplier >= crash_game_state['crash_point']:
            # Game crashed - any remaining players lost
            crash_game_state['status'] = 'crashed'
            
            # Record losses for remaining players
            for username, player_data in crash_game_state['active_players'].items():
                game_data = {
                    'game_id': secrets.token_hex(8),
                    'game_type': 'crash',
                    'username': username,
                    'bet': player_data['bet'],
                    'crash_point': crash_game_state['crash_point'],
                    'win_amount': 0,
                    'status': 'lost',
                    'timestamp': datetime.now()
                }
                save_game(game_data)
            
            # Add to history
            crash_game_state['game_history'].insert(0, crash_game_state['crash_point'])
            if len(crash_game_state['game_history']) > 10:
                crash_game_state['game_history'].pop()
            
            # After a delay, start waiting for next game
            time.sleep(3)  # Wait 3 seconds to show crash animation
            crash_game_state['active_players'] = {}
            crash_game_state['status'] = 'waiting'
        
        # If all active players have cashed out, end the game
        elif not crash_game_state['active_players'] and crash_game_state['status'] == 'active':
            # Add current multiplier to history
            crash_game_state['game_history'].insert(0, current_multiplier)
            if len(crash_game_state['game_history']) > 10:
                crash_game_state['game_history'].pop()
            
            # Reset for next game
            crash_game_state['status'] = 'waiting'

# Run the game status checker in a background thread
def run_crash_game_checker():
    while True:
        try:
            check_crash_game_status()
        except Exception as e:
            print(f"Error in crash game checker: {e}")
        time.sleep(0.1)  # Check every 100ms

# Start the checker thread when app starts
crash_checker_thread = threading.Thread(target=run_crash_game_checker, daemon=True)
crash_checker_thread.start()

@app.route('/crash/start', methods=['POST'])
def start_crash_game():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    data = request.get_json()
    bet = data.get('bet', 0)
    auto_cashout = data.get('auto_cashout', None)
    username = session['username']
    
    # Get user from MongoDB
    user = get_user(username)
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบผู้ใช้'})
    
    if bet <= 0 or bet > user["balance"]:
        return jsonify({'success': False, 'message': 'จำนวนเงินเดิมพันไม่ถูกต้อง'})
    
    # Deduct bet amount
    update_user_balance(username, -bet)
    
    with crash_game_lock:
        # Add player to waiting room if game is active or crashed
        # Or to active players if game is about to start
        player_data = {
            'bet': bet,
            'auto_cashout': auto_cashout,
            'join_time': time.time()
        }
        
        if crash_game_state['status'] == 'waiting':
            crash_game_state['waiting_players'][username] = player_data
            status = 'waiting'
        else:
            # Game in progress, add to waiting
            crash_game_state['waiting_players'][username] = player_data
            status = 'waiting'
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'status': status,
        'bet': bet,
        'auto_cashout': auto_cashout,
        'balance': user["balance"],
        'players_online': list(crash_game_state['players_online']),
        'game_history': crash_game_state['game_history']
    })

@app.route('/crash/status', methods=['GET'])
def crash_game_status():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    username = session['username']
    
    # Get user from MongoDB
    user = get_user(username)
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบผู้ใช้'})
    
    with crash_game_lock:
        # Update user's online status
        crash_game_state['players_online'].add(username)
        
        # Check player status
        is_active = username in crash_game_state['active_players']
        is_waiting = username in crash_game_state['waiting_players']
        
        # Prepare response
        response = {
            'success': True,
            'game_status': crash_game_state['status'],
            'current_multiplier': crash_game_state['current_multiplier'],
            'is_active': is_active,
            'is_waiting': is_waiting,
            'players_online': list(crash_game_state['players_online']),
            'active_players': list(crash_game_state['active_players'].keys()),
            'waiting_players': list(crash_game_state['waiting_players'].keys()),
            'game_history': crash_game_state['game_history'],
            'balance': user["balance"]
        }
        
        # Add crash point if game has crashed
        if crash_game_state['status'] == 'crashed':
            response['crash_point'] = crash_game_state['crash_point']
    
    return jsonify(response)

@app.route('/crash/cashout', methods=['POST'])
def crash_cashout():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    username = session['username']
    
    with crash_game_lock:
        # Check if player is active
        if username not in crash_game_state['active_players']:
            return jsonify({'success': False, 'message': 'ไม่ได้เล่นในรอบนี้'})
        
        if crash_game_state['status'] != 'active':
            return jsonify({'success': False, 'message': 'เกมไม่ได้กำลังเล่นอยู่'})
        
        # Get player data
        player_data = crash_game_state['active_players'][username]
        bet = player_data['bet']
        
        # Calculate winnings
        current_multiplier = crash_game_state['current_multiplier']
        win_amount = int(bet * current_multiplier)
        
        # Add winnings to user balance in MongoDB
        update_user_balance(username, win_amount)
        
        # Record game in history
        game_data = {
            'game_id': secrets.token_hex(8),
            'game_type': 'crash',
            'username': username,
            'bet': bet,
            'cashout_multiplier': current_multiplier,
            'win_amount': win_amount,
            'auto_cashout': False,
            'timestamp': datetime.now()
        }
        save_game(game_data)
        
        # Remove from active players
        del crash_game_state['active_players'][username]
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'multiplier': current_multiplier,
        'win_amount': win_amount,
        'balance': user["balance"]
    })

# Plinko (Drop the Ball) Logic
@app.route('/plinko/play', methods=['POST'])
def plinko_play():
    if 'username' not in session:
        return jsonify({'success': False, 'message': 'ไม่ได้เข้าสู่ระบบ'})
    
    data = request.get_json()
    bet = data.get('bet', 0)
    risk = data.get('risk', 'low')  # low, medium, high
    position = data.get('position', 0)  # User's chosen drop position
    username = session['username']
    
    # Get user from MongoDB
    user = get_user(username)
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบผู้ใช้'})
    
    if bet <= 0 or bet > user["balance"]:
        return jsonify({'success': False, 'message': 'จำนวนเงินเดิมพันไม่ถูกต้อง'})
    
    # Deduct bet amount
    update_user_balance(username, -bet)
    
    # Define multipliers based on risk
    multipliers = {
        'low': [1.5, 1.2, 1, 0.8, 0.5, 0.3, 0.5, 0.8, 1, 1.2, 1.5],
        'medium': [3, 1.5, 1, 0.5, 0.3, 0, 0.3, 0.5, 1, 1.5, 3],
        'high': [8, 3, 1, 0.5, 0.2, 0, 0.2, 0.5, 1, 3, 8]
    }
    
    # Determine final position and multiplier
    # For simplicity, we'll implement a house edge by biasing toward lower multipliers
    weights = []
    if risk == 'low':
        # More balanced weights for low risk
        weights = [10, 12, 15, 15, 12, 10, 12, 15, 15, 12, 10]
    elif risk == 'medium':
        # Medium risk has higher chance of low/zero multipliers
        weights = [5, 8, 12, 15, 18, 20, 18, 15, 12, 8, 5]
    else:  # high risk
        # High risk has higher chance of extreme outcomes (very low or very high)
        weights = [3, 5, 8, 10, 15, 25, 15, 10, 8, 5, 3]
    
    # Determine final landing position
    final_position = random.choices(range(10), weights=weights[:10])[0]  # Use only 10 positions
    
    # Get multiplier based on risk and position
    # Adjust index for 10 positions instead of 11
    multiplier_index = min(final_position, len(multipliers[risk]) - 1)
    multiplier = multipliers[risk][multiplier_index]
    
    # Calculate profit
    profit = int(bet * multiplier) - bet
    
    # Update balance if won
    if profit > 0:
        update_user_balance(username, bet + profit)
    
    # Create game record
    game_id = secrets.token_hex(8)
    game_data = {
        'game_id': game_id,
        'game_type': 'plinko',
        'username': username,
        'bet': bet,
        'risk': risk,
        'start_position': position,
        'final_position': final_position,
        'multiplier': multiplier,
        'profit': profit,
        'timestamp': datetime.now()
    }
    save_game(game_data)
    
    # Get updated user balance
    user = get_user(username)
    
    return jsonify({
        'success': True,
        'game_id': game_id,
        'final_position': final_position,
        'multiplier': multiplier,
        'profit': profit,
        'new_balance': user["balance"]
    })

if __name__ == '__main__':
    # Ensure the required directory exists
    os.makedirs('public', exist_ok=True)
    # Run the app
    app.run(host='0.0.0.0', port=8000, debug=True)

import sqlite3
import hashlib

DB_NAME = "smart_bin.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # --- Admins Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('train_admin', 'station_admin'))
        )
    ''')

    # --- Workers Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            status TEXT DEFAULT 'available' CHECK(status IN ('available', 'busy', 'offline')),
            points INTEGER DEFAULT 0
        )
    ''')

    # --- Bins Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bin_name TEXT NOT NULL,
            location TEXT NOT NULL,
            compartment TEXT NOT NULL,
            dry_level INTEGER DEFAULT 0 CHECK(dry_level BETWEEN 0 AND 100),
            wet_level INTEGER DEFAULT 0 CHECK(wet_level BETWEEN 0 AND 100),
            status TEXT DEFAULT 'normal' CHECK(status IN ('normal', 'full', 'maintenance')),
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- Tasks Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            bin_id INTEGER NOT NULL,
            compartment TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'in_progress', 'completed')),
            assigned_by TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (worker_id) REFERENCES workers(id),
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        )
    ''')

    # --- Alerts Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            sender TEXT NOT NULL,
            sender_role TEXT NOT NULL,
            receiver TEXT NOT NULL,
            alert_type TEXT DEFAULT 'info' CHECK(alert_type IN ('info', 'warning', 'urgent')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- Alert Reads Table (per-user tracking) ---
    # Each row = one user has seen one alert.
    # When a user polls, we insert rows for alerts they receive.
    # This way, train admin reading an alert does NOT affect station admin.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alert_reads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL,
            user_key TEXT NOT NULL,
            read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(alert_id, user_key)
        )
    ''')

    # --- Hardware Configuration Table ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bin_config (
            bin_id INTEGER PRIMARY KEY,
            stepper_speed INTEGER DEFAULT 800,
            open_time INTEGER DEFAULT 10000,
            cooldown_time INTEGER DEFAULT 5000,
            human_threshold INTEGER DEFAULT 50,
            bin_depth INTEGER DEFAULT 17,
            FOREIGN KEY (bin_id) REFERENCES bins(id)
        )
    ''')

    conn.commit()
    seed_data(conn, cursor)
    conn.close()
    print("✅ Database initialized successfully.")

def seed_data(conn, cursor):
    # --- Seed Admins ---
    admins = [
        ("Train Administrator", "train_admin", hash_password("admin123"), "train_admin"),
        ("Station Administrator", "station_admin", hash_password("admin123"), "station_admin"),
    ]
    for admin in admins:
        cursor.execute('''
            INSERT OR IGNORE INTO admins (name, username, password, role)
            VALUES (?, ?, ?, ?)
        ''', admin)

    # --- Seed Workers ---
    workers = [
        ("Ravi Kumar",   "worker1", hash_password("worker123")),
        ("Suresh Babu",  "worker2", hash_password("worker123")),
        ("Anita Singh",  "worker3", hash_password("worker123")),
    ]
    for worker in workers:
        cursor.execute('''
            INSERT OR IGNORE INTO workers (name, username, password)
            VALUES (?, ?, ?)
        ''', worker)

    # --- Seed Bin (prototype: single bin) ---
    cursor.execute('''
        INSERT OR IGNORE INTO bins (bin_name, location, compartment, dry_level, wet_level, status)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', ("Bin - 1", "Platform 1 - Coach S1", "S1", 0, 0, "normal"))

    # --- Seed Bin Config ---
    cursor.execute('''
        INSERT OR IGNORE INTO bin_config (bin_id, stepper_speed, open_time, cooldown_time, human_threshold, bin_depth)
        VALUES (1, 800, 10000, 5000, 50, 17)
    ''')

    conn.commit()
    print("✅ Demo data seeded.")

if __name__ == "__main__":
    init_db()

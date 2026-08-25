from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3
import hashlib
from datetime import datetime
from database import init_db, get_db, hash_password

app = Flask(__name__)
app.secret_key = "smartbin_secret_2024"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def login_required(role=None):
    if "user_id" not in session:
        return False
    if role and session.get("role") != role:
        return False
    return True

def user_key():
    """Unique string identifying the current logged-in user for alert read tracking."""
    return f"{session.get('role')}_{session.get('user_id')}"

def insert_alert(db, message, sender, sender_role, receiver, alert_type="info"):
    """Helper to insert an alert and return its id."""
    cur = db.execute("""
        INSERT INTO alerts (message, sender, sender_role, receiver, alert_type)
        VALUES (?, ?, ?, ?, ?)
    """, (message, sender, sender_role, receiver, alert_type))
    db.commit()
    return cur.lastrowid

# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        hashed   = hash_password(password)

        db   = get_db()
        user = db.execute(
            "SELECT * FROM admins WHERE username=? AND password=?",
            (username, hashed)
        ).fetchone()

        if user:
            session.update({"user_id": user["id"], "username": user["username"],
                            "name": user["name"], "role": user["role"]})
            db.close()
            return redirect(url_for("train_admin_dashboard" if user["role"] == "train_admin"
                                    else "station_admin_dashboard"))

        worker = db.execute(
            "SELECT * FROM workers WHERE username=? AND password=?",
            (username, hashed)
        ).fetchone()

        if worker:
            session.update({"user_id": worker["id"], "username": worker["username"],
                            "name": worker["name"], "role": "worker"})
            db.close()
            return redirect(url_for("worker_dashboard"))

        db.close()
        return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─────────────────────────────────────────────
# TRAIN ADMIN
# ─────────────────────────────────────────────

@app.route("/train-admin")
def train_admin_dashboard():
    if not login_required("train_admin"):
        return redirect(url_for("login"))
    db      = get_db()
    bin_row = db.execute("SELECT * FROM bins WHERE id=1").fetchone()
    workers = db.execute("SELECT * FROM workers ORDER BY name").fetchall()
    tasks   = db.execute("""
        SELECT t.*, w.name as worker_name, b.bin_name, b.location
        FROM tasks t JOIN workers w ON t.worker_id=w.id
        JOIN bins b ON t.bin_id=b.id
        ORDER BY t.created_at DESC
    """).fetchall()
    alerts  = db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 20").fetchall()
    bin_config = db.execute("SELECT * FROM bin_config WHERE bin_id=1").fetchone()
    db.close()
    return render_template("train_admin.html",
        bin=bin_row, workers=workers, tasks=tasks, alerts=alerts, name=session["name"], bin_config=bin_config)

@app.route("/train-admin/send-alert", methods=["POST"])
def send_alert():
    if not login_required("train_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json()
    msg  = data.get("message", "").strip()
    recv = data.get("receiver", "all")
    atype= data.get("alert_type", "info")
    if not msg:
        return jsonify({"success": False, "error": "Message cannot be empty"}), 400
    db = get_db()
    insert_alert(db, msg, session["name"], "train_admin", recv, atype)
    db.close()
    return jsonify({"success": True, "message": "Alert sent successfully"})

@app.route("/train-admin/assign-task", methods=["POST"])
def assign_task():
    if not login_required("train_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data        = request.get_json()
    worker_id   = data.get("worker_id")
    bin_id      = data.get("bin_id")
    description = data.get("description", "").strip()
    if not worker_id or not bin_id:
        return jsonify({"success": False, "error": "Worker and bin required"}), 400
    db      = get_db()
    bin_row = db.execute("SELECT compartment FROM bins WHERE id=?", (bin_id,)).fetchone()
    if not bin_row:
        db.close()
        return jsonify({"success": False, "error": "Bin not found"}), 404
    db.execute("""
        INSERT INTO tasks (worker_id, bin_id, compartment, description, assigned_by)
        VALUES (?, ?, ?, ?, ?)
    """, (worker_id, bin_id, bin_row["compartment"], description, session["name"]))
    db.execute("UPDATE workers SET status='busy' WHERE id=?", (worker_id,))
    db.commit()
    db.close()
    return jsonify({"success": True, "message": "Task assigned successfully"})

@app.route("/train-admin/control-bin", methods=["POST"])
def control_bin_train():
    if not login_required("train_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data   = request.get_json()
    bin_id = data.get("bin_id")
    action = data.get("action")
    if not bin_id or not action:
        return jsonify({"success": False, "error": "Missing fields"}), 400
    db = get_db()
    if action == "reset":
        db.execute("""UPDATE bins SET dry_level=0, wet_level=0, status='normal',
                   last_updated=CURRENT_TIMESTAMP WHERE id=?""", (bin_id,))
        msg = "Bin reset successfully"
    elif action == "maintenance":
        db.execute("""UPDATE bins SET status='maintenance',
                   last_updated=CURRENT_TIMESTAMP WHERE id=?""", (bin_id,))
        msg = "Bin marked for maintenance"
    else:
        db.close()
        return jsonify({"success": False, "error": "Invalid action"}), 400
    db.commit()
    db.close()
    return jsonify({"success": True, "message": msg})

# ─────────────────────────────────────────────
# STATION ADMIN
# ─────────────────────────────────────────────

@app.route("/station-admin")
def station_admin_dashboard():
    if not login_required("station_admin"):
        return redirect(url_for("login"))
    db      = get_db()
    bin_row = db.execute("SELECT * FROM bins WHERE id=1").fetchone()
    workers = db.execute("SELECT * FROM workers ORDER BY name").fetchall()
    tasks   = db.execute("""
        SELECT t.*, w.name as worker_name, b.bin_name, b.location
        FROM tasks t JOIN workers w ON t.worker_id=w.id
        JOIN bins b ON t.bin_id=b.id
        ORDER BY t.created_at DESC
    """).fetchall()
    alerts  = db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 20").fetchall()
    bin_config = db.execute("SELECT * FROM bin_config WHERE bin_id=1").fetchone()
    db.close()
    return render_template("station_admin.html",
        bin=bin_row, workers=workers, tasks=tasks, alerts=alerts, name=session["name"], bin_config=bin_config)

@app.route("/station-admin/send-alert", methods=["POST"])
def station_send_alert():
    if not login_required("station_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data  = request.get_json()
    msg   = data.get("message", "").strip()
    atype = data.get("alert_type", "info")
    if not msg:
        return jsonify({"success": False, "error": "Message cannot be empty"}), 400
    db = get_db()
    insert_alert(db, msg, session["name"], "station_admin", "workers", atype)
    db.close()
    return jsonify({"success": True, "message": "Alert sent to workers"})

@app.route("/station-admin/assign-task", methods=["POST"])
def station_assign_task():
    if not login_required("station_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data        = request.get_json()
    worker_id   = data.get("worker_id")
    bin_id      = data.get("bin_id")
    description = data.get("description", "").strip()
    if not worker_id or not bin_id:
        return jsonify({"success": False, "error": "Worker and bin required"}), 400
    db      = get_db()
    bin_row = db.execute("SELECT compartment FROM bins WHERE id=?", (bin_id,)).fetchone()
    if not bin_row:
        db.close()
        return jsonify({"success": False, "error": "Bin not found"}), 404
    db.execute("""
        INSERT INTO tasks (worker_id, bin_id, compartment, description, assigned_by)
        VALUES (?, ?, ?, ?, ?)
    """, (worker_id, bin_id, bin_row["compartment"], description, session["name"]))
    db.execute("UPDATE workers SET status='busy' WHERE id=?", (worker_id,))
    db.commit()
    db.close()
    return jsonify({"success": True, "message": "Task assigned successfully"})

@app.route("/station-admin/control-bin", methods=["POST"])
def control_bin_station():
    if not login_required("station_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data   = request.get_json()
    bin_id = data.get("bin_id")
    action = data.get("action")
    if not bin_id or not action:
        return jsonify({"success": False, "error": "Missing fields"}), 400
    db = get_db()
    if action == "reset":
        db.execute("""UPDATE bins SET dry_level=0, wet_level=0, status='normal',
                   last_updated=CURRENT_TIMESTAMP WHERE id=?""", (bin_id,))
        msg = "Bin reset successfully"
    elif action == "maintenance":
        db.execute("""UPDATE bins SET status='maintenance',
                   last_updated=CURRENT_TIMESTAMP WHERE id=?""", (bin_id,))
        msg = "Bin marked for maintenance"
    else:
        db.close()
        return jsonify({"success": False, "error": "Invalid action"}), 400
    db.commit()
    db.close()
    return jsonify({"success": True, "message": msg})

@app.route("/api/admin/reward-worker", methods=["POST"])
def reward_worker():
    if session.get("role") not in ("train_admin", "station_admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    data = request.get_json()
    worker_id = data.get("worker_id")
    points = data.get("points", 50) # default bonus is 50

    if not worker_id:
        return jsonify({"success": False, "error": "Worker required"}), 400

    db = get_db()
    # Check if worker exists
    worker = db.execute("SELECT name FROM workers WHERE id=?", (worker_id,)).fetchone()
    if not worker:
        db.close()
        return jsonify({"success": False, "error": "Worker not found"}), 404
        
    db.execute("UPDATE workers SET points = points + ? WHERE id=?", (points, worker_id))
    
    # Send them an alert that they got a bonus
    insert_alert(db, f"🎉 Bonus! You received {points} reward points from {session['name']}!",
                session["name"], session["role"], f"worker_{worker_id}", "info")
                
    db.commit()
    db.close()
    return jsonify({"success": True, "message": f"Added {points} points to {worker['name']}"})

# ─────────────────────────────────────────────
# WORKER
# ─────────────────────────────────────────────

@app.route("/worker")
def worker_dashboard():
    if not login_required("worker"):
        return redirect(url_for("login"))
    db    = get_db()
    worker = db.execute("SELECT status FROM workers WHERE id=?", (session["user_id"],)).fetchone()
    tasks = db.execute("""
        SELECT t.*, b.bin_name, b.location, b.dry_level, b.wet_level, b.status as bin_status
        FROM tasks t JOIN bins b ON t.bin_id=b.id
        WHERE t.worker_id=? ORDER BY t.created_at DESC
    """, (session["user_id"],)).fetchall()
    bin_row = db.execute("SELECT * FROM bins WHERE id=1").fetchone()
    alerts  = db.execute("""
        SELECT * FROM alerts WHERE receiver='all' OR receiver='workers'
        ORDER BY created_at DESC LIMIT 20
    """).fetchall()
    db.close()
    return render_template("worker.html",
        tasks=tasks, bin=bin_row, alerts=alerts, name=session["name"], worker_status=worker["status"])

@app.route("/worker/control-bin", methods=["POST"])
def control_bin_worker():
    if not login_required("worker"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data   = request.get_json()
    bin_id = data.get("bin_id")
    action = data.get("action")
    if not bin_id or not action:
        return jsonify({"success": False, "error": "Missing fields"}), 400
    db = get_db()
    if action == "reset":
        db.execute("""UPDATE bins SET dry_level=0, wet_level=0, status='normal',
                   last_updated=CURRENT_TIMESTAMP WHERE id=?""", (bin_id,))
        msg = "Bin reset successfully"
    elif action == "maintenance":
        db.execute("""UPDATE bins SET status='maintenance',
                   last_updated=CURRENT_TIMESTAMP WHERE id=?""", (bin_id,))
        msg = "Bin marked for maintenance"
    else:
        db.close()
        return jsonify({"success": False, "error": "Invalid action"}), 400
    db.commit()
    db.close()
    return jsonify({"success": True, "message": msg})

@app.route("/worker/toggle-duty", methods=["POST"])
def toggle_duty():
    if not login_required("worker"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    db = get_db()
    worker = db.execute("SELECT status FROM workers WHERE id=?", (session["user_id"],)).fetchone()
    if not worker:
        db.close()
        return jsonify({"success": False, "error": "Worker not found"}), 404
        
    current_status = worker["status"]
    
    if current_status == "busy":
        db.close()
        return jsonify({"success": False, "error": "Cannot go offline while busy with a task."}), 400
        
    new_status = "offline" if current_status == "available" else "available"
    
    db.execute("UPDATE workers SET status=? WHERE id=?", (new_status, session["user_id"]))
    db.commit()
    db.close()
    
    return jsonify({"success": True, "new_status": new_status, "message": f"Status changed to {new_status}"})

@app.route("/worker/update-task", methods=["POST"])
def update_task():
    if not login_required("worker"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data    = request.get_json()
    task_id = data.get("task_id")
    status  = data.get("status")
    if not task_id or status not in ("in_progress", "completed"):
        return jsonify({"success": False, "error": "Invalid data"}), 400
    db   = get_db()
    task = db.execute("SELECT * FROM tasks WHERE id=? AND worker_id=?",
                      (task_id, session["user_id"])).fetchone()
    if not task:
        db.close()
        return jsonify({"success": False, "error": "Task not found"}), 404
    db.execute("UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
               (status, task_id))
    if status == "completed":
        # Reward worker for completing task
        db.execute("UPDATE workers SET points = points + 10 WHERE id=?", (session["user_id"],))
        
        pending = db.execute("""SELECT COUNT(*) as cnt FROM tasks
            WHERE worker_id=? AND status IN ('pending','in_progress')""",
            (session["user_id"],)).fetchone()
        if pending["cnt"] == 0:
            db.execute("UPDATE workers SET status='available' WHERE id=?",
                       (session["user_id"],))
    db.commit()
    db.close()
    return jsonify({"success": True, "message": f"Task marked as {status}"})

# ─────────────────────────────────────────────
# SHARED API — ALERTS (per-user polling)
# ─────────────────────────────────────────────

@app.route("/api/alerts/poll")
def poll_alerts():
    """
    Per-user alert polling using alert_reads table.
    Each user gets their own unread alerts independently.
    Train admin reading an alert does NOT affect station admin or workers.
    """
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    role = session.get("role")
    ukey = user_key()
    db   = get_db()

    # Build receiver filter for this role
    if role == "train_admin":
        recv_filter = "receiver IN ('all', 'train_admin')"
    elif role == "station_admin":
        recv_filter = "receiver IN ('all', 'station_admin')"
    else:
        recv_filter = "receiver IN ('all', 'workers')"

    # Get alerts this user hasn't seen yet
    alerts = db.execute(f"""
        SELECT * FROM alerts
        WHERE {recv_filter}
        AND id NOT IN (
            SELECT alert_id FROM alert_reads WHERE user_key=?
        )
        ORDER BY created_at ASC
        LIMIT 10
    """, (ukey,)).fetchall()

    # Mark them as read for this specific user
    for a in alerts:
        try:
            db.execute("INSERT OR IGNORE INTO alert_reads (alert_id, user_key) VALUES (?,?)",
                       (a["id"], ukey))
        except Exception:
            pass
    if alerts:
        db.commit()

    db.close()
    return jsonify([dict(a) for a in alerts])

# ─────────────────────────────────────────────
# SHARED API — BINS
# ─────────────────────────────────────────────

@app.route("/api/bins")
def get_bins():
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    db   = get_db()
    bins = db.execute("SELECT * FROM bins ORDER BY id").fetchall()
    db.close()
    return jsonify([dict(b) for b in bins])

@app.route("/api/bins/update", methods=["POST"])
def update_bin_level():
    """
    ESP32 POSTs here to update bin levels.
    JSON: { "bin_id": 1, "dry_level": 45, "wet_level": 30 }

    Auto-generates alerts:
    - If dry compartment >= 90%  →  urgent alert to all
    - If wet compartment >= 90%  →  urgent alert to all
    - If both compartments >= 90% → single combined alert
    No auth required (hardware endpoint).
    """
    data      = request.get_json()
    bin_id    = data.get("bin_id")
    # ⚠️ HARDWARE FIX: Ultrasonic sensors are physically swapped on the bin.
    # The sensor wired as "dry" actually measures wet, and vice versa.
    # Swap here so the rest of the system sees correct labels.
    dry_level  = int(data.get("wet_level", 0))   # physically: wet sensor → dry field
    wet_level  = int(data.get("dry_level", 0))   # physically: dry sensor → wet field
    human_dist = int(data.get("human_dist", 0))

    if bin_id is None:
        return jsonify({"success": False, "error": "bin_id required"}), 400

    # Clamp values
    dry_level = max(0, min(100, dry_level))
    wet_level = max(0, min(100, wet_level))

    # Determine overall bin status
    if dry_level >= 90 and wet_level >= 90:
        status = "full"
    elif dry_level >= 90 or wet_level >= 90:
        status = "full"
    else:
        status = "normal"

    db = get_db()

    # Fetch previous levels to avoid duplicate alerts
    prev = db.execute("SELECT dry_level, wet_level FROM bins WHERE id=?", (bin_id,)).fetchone()
    prev_dry = prev["dry_level"] if prev else 0
    prev_wet = prev["wet_level"] if prev else 0

    db.execute("""
        UPDATE bins SET dry_level=?, wet_level=?, human_dist=?, status=?,
        last_updated=CURRENT_TIMESTAMP WHERE id=?
    """, (dry_level, wet_level, human_dist, status, bin_id))

    bin_row = db.execute("SELECT bin_name, location FROM bins WHERE id=?", (bin_id,)).fetchone()
    bin_name = bin_row["bin_name"] if bin_row else f"Bin {bin_id}"
    location = bin_row["location"] if bin_row else ""

    # --- Compartment-full alerts (only fire when crossing threshold, not every update) ---
    dry_just_full = dry_level >= 90 and prev_dry < 90
    wet_just_full = wet_level >= 90 and prev_wet < 90

    alert_msg = None
    if dry_just_full and wet_just_full:
        alert_msg = f"🚨 {bin_name} is critically full! (Dry: {dry_level}%, Wet: {wet_level}%) Immediate collection required."
    elif dry_just_full:
        alert_msg = f"🚨 {bin_name} is full! Dry compartment is {dry_level}% filled. Collect immediately."
    elif wet_just_full:
        alert_msg = f"🚨 {bin_name} is full! Wet compartment is {wet_level}% filled. Collect immediately."

    if alert_msg:
        for role in ["workers", "station_admin", "train_admin"]:
            insert_alert(db, alert_msg, "Admin System", "station_admin", role, "urgent")

    db.close()
    return jsonify({"success": True, "message": "Bin updated", "status": status})

# ─────────────────────────────────────────────
# HARDWARE CONTROL API (for ESP32 commands)
# ─────────────────────────────────────────────

# ESP32 polls this endpoint to receive commands (lid open/close, stepper)
_pending_commands = {}   # { bin_id: [cmd, ...] }

@app.route("/api/hardware/command", methods=["POST"])
def send_hw_command():
    """
    Web UI sends a hardware command.
    Commands: open_lid | close_lid | reset_stepper
    Requires login (any role).
    """
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data    = request.get_json()
    bin_id  = str(data.get("bin_id", 1))
    command = data.get("command", "")
    if command not in ("open_lid", "close_lid", "reset_stepper"):
        return jsonify({"success": False, "error": "Unknown command"}), 400
    if bin_id not in _pending_commands:
        _pending_commands[bin_id] = []
    _pending_commands[bin_id].append(command)
    return jsonify({"success": True, "message": f"Command '{command}' queued"})

@app.route("/api/hardware/poll/<int:bin_id>", methods=["GET"])
def poll_hw_commands(bin_id):
    """
    ESP32 polls this every second to receive pending commands.
    No auth (hardware endpoint). Returns list of commands then clears queue.
    Also returns hardware configuration settings.
    """
    key  = str(bin_id)
    cmds = _pending_commands.pop(key, [])
    
    db = get_db()
    conf = db.execute("SELECT * FROM bin_config WHERE bin_id=?", (bin_id,)).fetchone()
    bin_row = db.execute("SELECT dry_level, wet_level, human_dist FROM bins WHERE id=?", (bin_id,)).fetchone()
    db.close()
    
    response = {"commands": cmds}
    
    if conf:
        response.update({
            "stepper_speed": conf["stepper_speed"],
            "open_time": conf["open_time"],
            "cooldown_time": conf["cooldown_time"],
            "human_threshold": conf["human_threshold"],
            "bin_depth": conf["bin_depth"]
        })
        
    if bin_row:
        response.update({
            "dry_level": bin_row["dry_level"],
            "wet_level": bin_row["wet_level"],
            "human_dist": bin_row["human_dist"]
        })
        
    return jsonify(response)

@app.route("/api/hardware/config/update", methods=["POST"])
def update_hw_config():
    """
    Update hardware configuration for a bin.
    Requires login (station admin or train admin).
    """
    if "user_id" not in session or session.get("role") not in ["station_admin", "train_admin"]:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
        
    data = request.get_json()
    bin_id = data.get("bin_id", 1)
    
    stepper_speed = data.get("stepper_speed")
    open_time = data.get("open_time")
    cooldown_time = data.get("cooldown_time")
    human_threshold = data.get("human_threshold")
    bin_depth = data.get("bin_depth")
    
    if None in (stepper_speed, open_time, cooldown_time, human_threshold, bin_depth):
        return jsonify({"success": False, "error": "Missing configuration fields"}), 400
        
    db = get_db()
    db.execute("""
        UPDATE bin_config 
        SET stepper_speed=?, open_time=?, cooldown_time=?, human_threshold=?, bin_depth=?
        WHERE bin_id=?
    """, (stepper_speed, open_time, cooldown_time, human_threshold, bin_depth, bin_id))
    db.commit()
    db.close()
    
    return jsonify({"success": True, "message": "Hardware configuration updated successfully!"})

@app.route("/api/tasks/poll")
def poll_tasks():
    if not login_required("worker"):
        return jsonify({"error": "Unauthorized"}), 401
    db    = get_db()
    tasks = db.execute("""
        SELECT t.*, b.bin_name, b.location, b.dry_level, b.wet_level
        FROM tasks t JOIN bins b ON t.bin_id=b.id
        WHERE t.worker_id=? ORDER BY t.created_at DESC
    """, (session["user_id"],)).fetchall()
    db.close()
    return jsonify([dict(t) for t in tasks])

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)

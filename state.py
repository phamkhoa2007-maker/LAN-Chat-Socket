import threading
import time

# ==========================================
# 1. LOCAL PEER INFORMATION
# ==========================================
my_name = ""
my_tcp_port = 5000
my_status = "online"  # Allowed values: "online" or "away"
running = True        # Global flag to stop all background threads on /quit

# ==========================================
# 2. SHARED DATA STRUCTURES
# ==========================================
# Online peers discovered via UDP broadcast
# Format: { "Minh": {"ip": "192.168.1.12", "port": 5002, "status": "online", "last_seen": 1732170000.0} }
online_peers = {}

# Name of the peer currently open in chat (None if in lobby)
current_partner = None

# Active TCP sockets mapped by peer name: { "Minh": <socket_object> }
active_connections = {}

# Chat logs per peer: { "Minh": ["[Minh] Hi", "[Khoa] Hello"] }
chat_history = {}

# Unread message counter for peers who are NOT the current_partner: { "Huy": 2 }
unread_counts = {}

# ==========================================
# 3. THREAD SYNCHRONIZATION LOCKS
# ==========================================
state_lock = threading.Lock()   # Protects online_peers, chat_history, unread_counts, etc.
screen_lock = threading.Lock()  # Protects console output from overlapping prints

# ==========================================
# 4. THREAD-SAFE HELPER FUNCTIONS
# ==========================================
def update_peer(name, ip, tcp_port, status):
    """
    Updates or adds a peer when a UDP HELLO packet arrives.
    Returns True if this is a newly discovered peer.
    """
    with state_lock:
        is_new = name not in online_peers
        online_peers[name] = {
            "ip": ip,
            "port": int(tcp_port),
            "status": status,
            "last_seen": time.time(),
        }
        return is_new

def remove_timed_out_peers(timeout=10.0):
    """
    Removes peers that have not sent a UDP HELLO packet within the timeout window.
    Returns a list of removed peer names.
    """
    now = time.time()
    removed = []
    with state_lock:
        for name in list(online_peers.keys()):
            if now - online_peers[name]["last_seen"] > timeout:
                del online_peers[name]
                removed.append(name)
    return removed

def get_peers_snapshot():
    """Returns a thread-safe shallow copy of online_peers (used for /list)."""
    with state_lock:
        return dict(online_peers)
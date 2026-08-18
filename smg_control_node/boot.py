"""boot.py — preload secure_node (with all crypto C modules) before main.py."""
try:
    from secure_node import SecureNode
except ImportError:
    pass

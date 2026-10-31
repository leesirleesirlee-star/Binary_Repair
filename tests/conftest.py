import os

# Deterministic session token for the non-browser test suite. These must be set
# before app.main is imported (module-level SESSION_TOKEN is read at import).
os.environ.setdefault("BINARYREPAIR_SESSION_TOKEN", "test-session-token")

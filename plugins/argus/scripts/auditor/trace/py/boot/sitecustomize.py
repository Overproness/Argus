"""Loaded automatically by every Python interpreter whose PYTHONPATH starts here."""
import sys

try:
    from auditor.trace.py.tracer import install_from_env
    install_from_env()
except Exception as e:  # never break the traced program
    print(f"argus: tracer not installed: {e}", file=sys.stderr)

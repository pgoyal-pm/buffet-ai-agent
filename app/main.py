"""
Main FastAPI application entry point for Compounder Dashboard

Initializes database, sets up routing, and handles lifecycle events.
Designed for Docker deployment with PostgreSQL or SQLite support.
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.api.routes import create_app

# Database path (can be overridden via environment variable)
DB_PATH = os.getenv('DATABASE_URL', 'compounder.db')

def main():
    """Initialize and start the application."""
    print(f"Starting Compounder Dashboard v1.0...")
    print(f"Database path: {DB_PATH}")
    
    # Create app instance
    app = create_app(DB_PATH)
    
    return app

if __name__ == '__main__':
    import uvicorn
    config = uvicorn.Config(main(), host='0.0.0.0', port=8000)
    server = uvicorn.Server(config)
    server.run()

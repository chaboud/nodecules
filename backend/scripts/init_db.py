#!/usr/bin/env python3
"""
Database initialization and validation script.
Ensures all tables are created and the database is properly set up.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import inspect, text
from nodecules.models.database import engine, Base
from nodecules.models.schemas import Graph, Execution, DataObject, Annotation, User, ContextStorage
from nodecules.core.content_addressable_context import ImmutableContext


def check_database_connection():
    """Test database connection."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ Database connection successful")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False


def get_existing_tables():
    """Get list of existing tables."""
    inspector = inspect(engine)
    return set(inspector.get_table_names())


def get_required_tables():
    """Get list of required tables from models."""
    # Import all models to ensure they're registered
    required_tables = set()
    for table in Base.metadata.tables.values():
        required_tables.add(table.name)
    return required_tables


def create_missing_tables():
    """Create any missing tables."""
    existing_tables = get_existing_tables()
    required_tables = get_required_tables()
    missing_tables = required_tables - existing_tables
    
    if missing_tables:
        print(f"📝 Creating missing tables: {', '.join(missing_tables)}")
        Base.metadata.create_all(engine)
        return True
    else:
        print("✅ All required tables exist")
        return False


def validate_tables():
    """Validate that all expected tables exist with correct structure."""
    existing_tables = get_existing_tables()
    required_tables = get_required_tables()
    
    print(f"📊 Database Status:")
    print(f"   Required tables: {len(required_tables)}")
    print(f"   Existing tables: {len(existing_tables)}")
    
    missing = required_tables - existing_tables
    if missing:
        print(f"❌ Missing tables: {', '.join(missing)}")
        return False
    
    extra = existing_tables - required_tables - {"alembic_version"}
    if extra:
        print(f"⚠️  Extra tables: {', '.join(extra)}")
    
    print(f"✅ All required tables present: {', '.join(sorted(required_tables))}")
    return True


def main():
    """Main initialization function."""
    print("🔧 Initializing Nodecules Database...")
    print("=" * 50)
    
    # Step 1: Check connection
    if not check_database_connection():
        sys.exit(1)
    
    # Step 2: Show current state
    existing_tables = get_existing_tables()
    required_tables = get_required_tables()
    
    print(f"\n📋 Expected Tables ({len(required_tables)}):")
    for table in sorted(required_tables):
        status = "✅" if table in existing_tables else "❌"
        print(f"   {status} {table}")
    
    # Step 3: Create missing tables
    print("\n🛠️  Fixing Database Schema...")
    tables_created = create_missing_tables()
    
    # Step 4: Final validation
    print("\n🔍 Final Validation...")
    if validate_tables():
        print("\n🎉 Database initialization completed successfully!")
        print("\n💡 Next steps:")
        print("   1. Start the backend: uvicorn nodecules.main:app --reload")
        print("   2. Access web interface: http://localhost:3000")
        print("   3. View API docs: http://localhost:8000/docs")
    else:
        print("\n❌ Database initialization failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
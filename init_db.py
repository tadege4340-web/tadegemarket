
from app import app, db

def initialize_database():
    with app.app_context():
        print("Connecting to the configured database...")

        db.create_all()

        print("Database initialization completed!")
        print("Missing tables have been created where necessary.")

if __name__ == "__main__":
    initialize_database()

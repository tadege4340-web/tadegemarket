
from pathlib import Path
from dotenv import load_dotenv
import os
import psycopg

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

try:
    connection = psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        dbname=os.getenv("DB_NAME"),
        sslmode=os.getenv("DB_SSLMODE", "require"),
        connect_timeout=15
    )

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT
                version(),
                current_database(),
                current_user;
        """)

        result = cursor.fetchone()

        print("\nSUCCESS: Connected to Aiven PostgreSQL!")
        print("PostgreSQL:", result[0])
        print("Database:", result[1])
        print("User:", result[2])

    connection.close()

except Exception as error:
    print("\nDATABASE CONNECTION FAILED")
    print("Error:", error)

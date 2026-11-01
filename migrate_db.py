from app import app, db
from sqlalchemy import inspect, text

with app.app_context():

    print("Database:", db.engine.url)

    inspector = inspect(db.engine)

    columns = [
        column["name"]
        for column in inspector.get_columns("product")
    ]

    print("Old columns:", columns)

    if "created_at" not in columns:

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE product ADD COLUMN created_at DATETIME"
                )
            )

        print("created_at column added!")

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE product SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
            )
        )

    print("Migration completed successfully!")
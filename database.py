import sqlite3

connection = sqlite3.connect("fitness.db")
cursor = connection.cursor()

# User Information
cursor.execute("""
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY,
    age INTEGER,
    gender TEXT,
    height_cm REAL,
    weight_kg REAL,
    fitness_level TEXT,
    Avail_days TEXT,
    duration INTEGER,
    training_goal TEXT,
    distance INTEGER,
    pace TEXT,
    avg_hr INTEGER,
    health_injury_history TEXT,
    avail_equipment TEXT
)
""")

# Workout records
cursor.execute("""
CREATE TABLE IF NOT EXISTS workout (
    id INTEGER PRIMARY KEY,
    date TEXT,
    exercise TEXT,
    sets INTEGER,
    reps INTEGER,
    weight_kg REAL
)
""")

# Fitness test records
cursor.execute("""
CREATE TABLE IF NOT EXISTS fitness_test (
    id INTEGER PRIMARY KEY,
    date TEXT,
    run_2_4km_seconds INTEGER,
    pushups INTEGER,
    situps INTEGER
)
""")

connection.commit()
connection.close()

print("Database and user table created!")
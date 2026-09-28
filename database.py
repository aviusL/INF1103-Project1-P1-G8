import sqlite3

connection = sqlite3.connect("fitness.db")

cursor = connection.cursor()

connection.commit()
connection.close()

print("Database created successfully!")
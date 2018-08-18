import connections
import sqlite3


# Load the database and create it if needed
db = sqlite3.connect('server.db')

cursor = db.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE,
                       username TEXT, password TEXT, image BLOB, last_update BLOB)
''')  #TODO: ENCRYPT PASSWORD !!!
cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations(uuid BLOB, user INTEGER)
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversationsdata(uuid BLOB, name TEXT, image BLOB)
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversationsevents(uuid BLOB, event TEXT, time BLOB, arg1 TEXT, arg2 TEXT)
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS 
        messages(conversation BLOB, message TEXT, sender INTEGER, time BLOB, file_uuid BLOB, file_name STRING)
''')
db.commit()
db.close()

print("Database loaded !")

# Start the server
serv = connections.Server()
connections.server = serv
serv.start()

# TODO: Shutdown the server (server.close()) + close database

# TODO: ALL:
"""
- Check xml security vulnerabilities : https://docs.python.org/3.3/library/xml.html#xml-vulnerabilities"""
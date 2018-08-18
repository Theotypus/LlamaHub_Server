import xml.etree.ElementTree as ET
import connections
import sqlite3
import uuid
import time


""" Function to process incoming commands """


def check_login(guest, xml):
    """Processes only 'SignUp' and 'LogIn' commands"""
    db = sqlite3.connect('server.db')
    cursor = db.cursor()
    command = xml.getroot()

    if command.tag == 'LogIn':
        username = command.attrib['Username']
        password = command.attrib['Password']

        # Check if the account exists
        cursor.execute('''SELECT username FROM users''')
        if (username,) not in cursor.fetchall():
            guest.send(report_error(xml, "No account registered with this username !"))
            db.close()
            return

        for user in connections.server.connected:   #TODO : Delete?
            if user.username == username:  # If the user is already connected, return an error
                guest.send(report_error(xml, "This account is already connected !"))
                db.close()
                return

        # Check password
        cursor.execute('''SELECT password FROM users WHERE username = ?''', (username,))
        if not cursor.fetchone()[0] == password:
            guest.send(report_error(xml, "Wrong password !"))
            db.close()
            return

        # Change the 'Guest' object to a 'User' object and initialize it
        cursor.execute('''SELECT id, username FROM users WHERE username = ?''', (username,))   # Retrieve the user's id
        data = cursor.fetchone()
        user = connections.User(data[0], data[1], socket=guest.socket)
        connections.server.new_connections.remove(guest)
        guest.stopped = True  # We can't call disconnect() as we don't wanna close the connection
        connections.server.connected.append(user)
        user.connected = True
        user.send(confirm_command(xml, user.id))   # Confirm the log in
        user.update(float(command.attrib['Time']))
        user.receive()
        db.close()
        return

    elif command.tag == 'SignUp':
        username = command.attrib['Username']

        # Check if the username is available
        cursor.execute('''SELECT username FROM users''')
        if (username,) in cursor.fetchall():
            guest.send(report_error(xml, "This username is already used!"))
            db.close()
            return

        # Register the new user
        cursor.execute('''INSERT INTO users(username, password, last_update)
                          VALUES(?,?,0)''', (username, command.attrib['Password']))
        db.commit()

        cursor.execute('''SELECT id FROM users WHERE username = ?''', (username,))    # Retrieve the user's id
        id = cursor.fetchone()[0]
        user = connections.User(id, username, socket=guest.socket)  # Create a new user (see 'log in')
        guest.stopped = True
        connections.server.connected.append(user)
        user.connected = True
        user.send(confirm_command(xml, user.id))
        user.receive()
    db.close()


def process(client, xml):
    """Processes all the other commands"""
    db = sqlite3.connect('server.db')
    cursor = db.cursor()
    command = xml.getroot()

    if command.tag == 'Message':
        conv_id = command.attrib['Conversation']

        # Check if the user is in the conversation
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ? AND user = ?''', (conv_id, client.id))
        if len(cursor.fetchall()) == 0:
            client.send(report_error(xml, "Wrong conversation id !"))
            return

        timestamp = time.time()
        # Save the message to the db
        cursor.execute('''INSERT INTO messages(conversation, message, sender, time)
                                VALUES(?,?,?,?)''', (conv_id, command.text, client.id, timestamp))

        # Get the other participants' id
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ?''', (conv_id,))

        # Send them the message if they're online
        for user in cursor.fetchall():
            for clt in connections.server.connected:
                if clt.id == user[0]:
                    clt.send(message(conv_id, command.text, client.id, timestamp))

        db.commit()

    elif command.tag == 'NewConversation':    #TODO: Limit image size
        # Read all the participants and get their username
        timestamp = time.time()

        participants = []
        image = None
        participants.append((client.id, client.username))  # Don't forget to add the 'creator'
        for child in command.iter():
            if child is not command:
                if child.tag == 'Participant':
                    user_id = int(child.attrib['Id'])
                    cursor.execute('''SELECT username FROM users WHERE id = ?''', (user_id,))
                    participants.append((user_id, cursor.fetchone()[0]))
                elif child.tag == 'Image':
                    image = child.text

        conv_id = uuid.uuid1().hex    # Generate an unique id
        # Save the conversation to the db (with the image if it exists)
        if image is not None:
            cursor.execute('''INSERT INTO conversationsdata(uuid, name, image)
                                                VALUES(?,?,?)''', (conv_id, command.attrib['Name'], image))
        else:
            cursor.execute('''INSERT INTO conversationsdata(uuid, name)
                                                VALUES(?,?)''', (conv_id, command.attrib['Name']))

        cursor.execute('''INSERT INTO conversationsevents(uuid, event, time, arg1)
                                                VALUES(?,?,?,?)''', (conv_id, "CREATION", timestamp, client.id))

        # Update the participants if they're online
        for (id, username) in participants:
            for clt in connections.server.connected:
                if clt.id == id:
                    clt.send(new_conversation(conv_id, command.attrib['Name'],
                                              participants, client.id, client.username, timestamp, image=image))
            cursor.execute('''INSERT INTO conversations(uuid, user)
                                        VALUES(?,?)''', (conv_id, id))
        db.commit()

    elif command.tag == 'Search':
        # Create the sql query
        if command.text is not None:
            search = '%'+command.text+'%'
        else:
            search = "%"
        cursor.execute('''SELECT id, username FROM users WHERE username LIKE ?''', (search,))

        # List all the users who matches the search
        results = []
        for (id, username) in cursor.fetchall():
            if not id == client.id:
                results.append((id, username))
        client.send(search_results(results))

    elif command.tag == 'Leave':    #TODO: Delete conv when last user leaves
        timestamp = time.time()
        conv_id = command.attrib['Conversation']

        # Check if the user is in the conversation
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ? AND user = ?''', (conv_id, client.id))
        if len(cursor.fetchall()) == 0:
            client.send(report_error(xml, "Wrong conversation id !"))
            return

        # Update the db
        cursor.execute('''DELETE FROM conversations WHERE uuid = ? AND user = ?''', (conv_id, client.id))
        cursor.execute('''INSERT INTO conversationsevents(uuid, event, time, arg1)
                                                VALUES(?,?,?,?)''', (conv_id, "LEAVE", timestamp, client.id))
        db.commit()

        # Update the online participants
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ?''', (conv_id,))
        for (id,) in cursor.fetchall():
            for clt in connections.server.connected:
                if clt.id == int(id):
                    clt.send(user_left(conv_id, client.id, timestamp))

    elif command.tag == 'ChangeName':
        timestamp = time.time()

        conv_id = command.attrib['Conversation']
        new_name = command.text

        # Check if the user is in the conversation
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ? AND user = ?''', (conv_id, client.id))
        if len(cursor.fetchall()) == 0:
            client.send(report_error(xml, "Wrong conversation id !"))
            return

        # Update the db
        cursor.execute('''UPDATE conversationsdata SET name = ? WHERE uuid = ?''', (new_name, conv_id))
        cursor.execute('''INSERT INTO conversationsevents(uuid, event, time, arg1, arg2)
                                       VALUES(?,?,?,?,?)''', (conv_id, "CHANGE_NAME", timestamp, new_name, client.id))
        db.commit()

        # Update the other online participants
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ? AND user != ?''', (conv_id, client.id))
        for (id,) in cursor.fetchall():
            for clt in connections.server.connected:
                if clt.id == int(id):
                    clt.send(change_name(conv_id, new_name, timestamp, client.id))

    elif command.tag == 'Add':
        timestamp = time.time()

        conv_id = command.attrib['Conversation']
        user_id = command.attrib['User']

        # Check if the user is in the conversation
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ? AND user = ?''', (conv_id, client.id))
        if len(cursor.fetchall()) == 0:
            client.send(report_error(xml, "Wrong conversation id !"))
            return

        # Update the db
        cursor.execute('''INSERT INTO conversations (uuid, user) VALUES (?, ?)''', (conv_id, user_id))
        cursor.execute('''INSERT INTO conversationsevents(uuid, event, time, arg1, arg2)
                                                VALUES(?,?,?,?,?)''', (conv_id, "ADD", timestamp, user_id, client.id))
        db.commit()

        # List the participants
        cursor.execute('''SELECT id, username FROM conversations 
                JOIN users ON conversations.user = users.id WHERE uuid = ?''', (conv_id,))
        participants = cursor.fetchall()

        # Retrieve the username of the added user
        cursor.execute('''SELECT username FROM users WHERE id = ?''', (user_id,))
        name = cursor.fetchone()[0]

        # Update the added user
        for clt in connections.server.connected:
            if clt.id == int(user_id):
                cursor.execute('''SELECT name, image FROM conversationsdata WHERE uuid = ?''', (conv_id,))
                (name, image) = cursor.fetchone()
                cursor.execute('''SELECT time, arg1 FROM conversationsevents 
                        WHERE uuid = ? AND event = "CREATION"''', (conv_id,))
                (creation_time, creator) = cursor.fetchone()
                clt.send(new_conversation(conv_id, name, participants, creator, creation_time, image=image))

        # Update the other online participants
        cursor.execute('''SELECT user FROM conversations WHERE uuid = ? AND user != ?''', (conv_id, client.id))
        for (id,) in cursor.fetchall():
            for clt in connections.server.connected:
                if clt.id == int(id):
                    clt.send(add(conv_id, user_id, name, client.id, timestamp))
            cursor.execute('''SELECT username FROM users WHERE id = ?''', (user_id,))
            participants.append((id, cursor.fetchone()[0]))

    else:
        print('Unknown command received : ' + command.tag)

    db.close()


"""def process_file(client, uuid, file):
    db = sqlite3.connect('server.db')
    cursor = db.cursor()
    cursor.execute('''SELECT uuid FROM conversations WHERE user = ?''', (client.id,))
    for (conv,) in cursor.fetchall():
        cursor.execute('''SELECT file_name FROM messages WHERE conversation = ? AND file_uuid = ?''', (conv, uuid))
        print(cursor.fetchall())   #TODO: FINISH!

    if True:

    db.close()"""


""" Function to send commands
 Use like this :
    client.send(some_command(...))"""


def confirm_command(xml, id):
    """Confirm the success of a command (log in or sign up usually)"""
    command = ET.Element("Response")
    command.set('Success', "true")
    command.set('Command', xml.getroot().tag)
    command.set('Id', str(id))
    return command


def report_error(xml, error):
    """Send an error"""
    command = ET.Element("Error")
    command.set('Command', xml.getroot().tag)
    command.text = error
    return command


def message(conversation, message, sender, timestamp):
    """Send a new message"""
    command = ET.Element('Message')
    command.attrib['Sender'] = str(sender)
    command.attrib['Time'] = str(timestamp)
    command.attrib['Conversation'] = conversation
    command.text = message
    return command


def new_conversation(id, name, participants, creator, creator_username, time, image=None):
    """Send the creation of a new conversation"""
    command = ET.Element('NewConversation')
    command.attrib['Name'] = name
    command.attrib['Id'] = id
    command.attrib['Creator'] = str(creator)
    command.attrib['C_Username'] = str(creator_username)
    command.attrib['Time'] = str(time)
    for participant in participants:
        item = ET.SubElement(command, 'Participant')
        item.attrib['Id'] = str(participant[0])
        item.attrib['Username'] = participant[1]
    if image is not None:
        img = ET.SubElement(command, 'Participant')
        img.text = image
    return command


def search_results(results):
    """Send the results of a query"""
    command = ET.Element('Search')
    for result in results:
        item = ET.SubElement(command, 'Participant')
        item.attrib['Id'] = str(result[0])
        item.attrib['Username'] = str(result[1])
    return command


def user_left(conv, user, time):
    """Send to report an user has left a conversation"""
    command = ET.Element('UserLeft')
    command.attrib['Conversation'] = conv
    command.attrib['User'] = str(user)
    command.attrib['Time'] = str(time)
    return command


def change_name(conv, name, time, user):
    """Send to report a conversation's name has changed"""
    command = ET.Element('ChangeName')
    command.attrib['Conversation'] = conv
    command.attrib['Time'] = str(time)
    command.attrib['User'] = str(user)
    command.text = name
    return command


def add(conv, id, username, added_by, time):
    """Send to report a user has been added"""
    command = ET.Element('Add')
    command.attrib['Conversation'] = conv
    command.attrib['Id'] = str(id)
    command.attrib['Username'] = username
    command.attrib['AddedBy'] = str(added_by)
    command.attrib['Time'] = str(time)
    return command
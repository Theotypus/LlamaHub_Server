import socket
import threading
import command
import xml.etree.ElementTree as ET
import sqlite3

server = None  # Current instance of the server (only one server can be running at once)


class Server:

    def __init__(self):
        self.new_connections = []  # List (<Guest>) of connected client that aren't logged in yet
        self.connected = []  # List (<User>) of all users currently online
        self.socket = None

    def start(self):
        """Starts the server (obviously... )"""

        # Create a TCP socket
        self.socket = socket.socket()

        # Bind the socket to the port
        server_address = ('localhost', 4242)
        print('Starting on %s port %s' % server_address)
        self.socket.bind(server_address)

        # Listen for incoming connections
        self.socket.listen(1)

        while True:
            # Wait for a connection
            print('Waiting for a connection...')
            connection, client_address = self.socket.accept()

            print('Connection from ', client_address)

            # The new connection is stored in a 'Guest' object until the user logs in
            guest = Guest(connection)
            self.new_connections.append(guest)

            # Listen for incoming commands
            t = threading.Thread(target=guest.receive)
            t.start()

    def stop(self):
        """Disconnect all users/guests and closes the server"""

        print("Closing server...")
        for client in self.new_connections:
            client.disconnect()
        for client in self.connected:
            client.disconnect()

        self.socket.close()


class Guest:
    """Class that represents a client that hasn't logged in yet,
    basically a 'User' with less permissions"""

    def __init__(self, socket):
        self.socket = socket
        self.stopped = False  # Set to 'True' to stop listening for incoming commands

    def receive(self):
        while not self.stopped:
            try:
                data = self.socket.recv(1)  # Wait and read the first byte of the message (blocking call)
            except ConnectionResetError:
                self.disconnect()  # User is disconnected
                return
            if not data:  # TODO: Useful?
                self.disconnect()  # No idea why the fuck that is here but there must be a reason
                return

            # The first bytes of the message (until the ':') indicates its length (in bytes)
            # This is useful to ensure we receive the message entirely before processing it
            received = None
            while received is None or not received.decode('utf8') == ':':  # Read the length
                received = self.socket.recv(1)
                data += received

            # Receive data until the number of bytes is filled (to make sure we have the full message)
            xml_str = self.socket.recv(int(data.decode('utf8')[:-1]))

            # Build the xml document and process it
            if not xml_str == "":
                print('Received from guest:')
                print(xml_str)
                #TODO: Restore
                """try:
                    xml = ET.ElementTree(ET.fromstring(xml_str))
                    # Only the sign up and log in commands are available to guests before they sign in
                    command.check_login(self, xml)
                except Exception as e:
                    print(e)
                    self.disconnect()"""
                xml = ET.ElementTree(ET.fromstring(xml_str))
                command.check_login(self, xml)

    def send(self, xml):
        """Sends a xml command to this client"""
        xml_str = ET.tostring(xml, encoding='utf8', method='xml')  # Converts the xml doc to a string
        print('Sending to guest:')
        #print(xml_str)
        length = str(len(xml_str))+':'  # Send the length of the message first (see 'receive' for more)
        self.socket.send(length.encode('utf-8'))
        self.socket.send(xml_str)

    def disconnect(self):
        """Disconnects this client"""
        print('Guest disconnected')
        self.socket.close()
        if self in server.new_connections:
            server.new_connections.remove(self)


class User:

    def __init__(self, id, username, socket=None):
        self.id = id
        self.username = username
        self.socket = socket
        if socket is not None and self in server.connected:
            self.connected = True
        else:
            self.connected = False

    def update(self, time):
        """Called when the user connects. It sends:
        -   The list of new conversations
        -   # TODO: The new messages for each conversations"""
        print('---- Updating client '+self.username+' ----')

        db = sqlite3.connect('server.db')
        cursor = db.cursor()

        # Updating conversations
        cursor.execute('''SELECT conversations.uuid FROM conversations 
                JOIN conversationsevents ON conversations.uuid = conversationsevents.uuid 
                WHERE event = "CREATION" AND time > ? AND user = ?''', (time, self.id))
        conversations = cursor.fetchall()
        for (uuid,) in conversations:
            cursor.execute('''SELECT name, image FROM conversationsdata WHERE uuid = ?''', (uuid,))
            (name, image) = cursor.fetchone()
            cursor.execute('''SELECT arg1, time FROM conversationsevents 
                        WHERE event = "CREATION" AND uuid = ?''', (uuid,))
            (creator, time) = cursor.fetchone()
            cursor.execute('''SELECT username FROM users WHERE id = ?''', (creator,))
            (creator_name,) = cursor.fetchone()

            cursor.execute('''SELECT user FROM conversations WHERE uuid = ?''', (uuid,))
            participants = []
            for (id,) in cursor.fetchall():
                cursor.execute('''SELECT username FROM users WHERE id = ?''', (id,))
                participants.append((id, cursor.fetchone()[0]))
            self.send(command.new_conversation(uuid, name, participants, creator, creator_name, time, image))

        # Listing conversations
        cursor.execute('''SELECT uuid FROM conversations WHERE user = ?''', (self.id,))

        for (conv,) in cursor.fetchall():
            # Updating messages
            cursor.execute('''SELECT time FROM conversationsevents 
                    WHERE uuid = ? AND event = "ADD" AND arg1 = ?''', (conv, self.id))
            print(cursor.fetchone())
            cursor.execute('''SELECT conversation, message, sender, time FROM messages 
                        WHERE conversation = ? AND time > ? AND sender != ?''', (conv, time, self.id))
            for (uuid, message, sender, time) in cursor.fetchall():
                self.send(command.message(uuid, message, sender, time))

            # Updating events
            cursor.execute('''SELECT uuid, event, time, arg1, arg2 FROM conversationsevents 
                        WHERE uuid = ? AND time > ?''', (conv, time))
            events = cursor.fetchall()
            print(events)
            for (uuid, event, time, arg1, arg2) in events:
                if event == 'CREATION':
                    pass
                elif event == 'ADD':
                    if int(arg2) == self.id:
                        continue
                    cursor.execute('''SELECT username FROM users WHERE id = ?''', (arg1,))
                    self.send(command.add(uuid, arg1, cursor.fetchone()[0], arg2, time))
                elif event == 'LEAVE':
                    if int(arg1) == self.id:
                        continue
                    self.send(command.user_left(uuid, arg1, time))
                elif event == 'CHANGE_NAME':
                    if int(arg2) == self.id:
                        continue
                    self.send(command.change_name(uuid, arg1, time, arg2))

        print("--- Done ---")

    def send(self, xml):
        """See the class 'Guest'"""
        xml_str = ET.tostring(xml, encoding='utf8', method='xml')
        print('Sending to '+self.username+':')
        print(xml_str)
        length = str(len(xml_str))+':'
        self.socket.send(length.encode('utf-8'))
        self.socket.send(xml_str)

    def receive(self):
        """See the class 'Guest'"""
        while True:
            data = None
            try:
                data = self.socket.recv(1)
            except ConnectionResetError:
                self.disconnect()
                return
            if not data:  # TODO: Useful?
                self.disconnect()
                return
            received = None
            while received is None or not received.decode('utf8') == ':':
                received = self.socket.recv(1)
                data += received

            self.receive_xml(int(data.decode('utf8')[:-1]))

            """if data[0] == 'F':
                self.receive_file(int(data.decode('utf8')[1:-1]))
            else:
                self.receive_xml(int(data.decode('utf8')[:-1]))"""

    def receive_xml(self, length):
        xml_str = self.socket.recv(length)

        if not xml_str == "":
            print('Received from ' + self.username + ':')
            #print(length)
            print(xml_str)
            # TODO: Restore
            """try:
                xml = ET.ElementTree(ET.fromstring(xml_str))
                # Other commands are allowed as the user is logged in
                command.process(self, xml)
            except Exception as e:
                print(e)
                self.disconnect()"""
            xml = ET.ElementTree(ET.fromstring(xml_str))
            command.process(self, xml)

    """def receive_file(self, length):
        data = ""
        received = None
        while received is None or not received.decode('utf8') == ':':
            received = self.socket.recv(1)
            data += received
        uuid = data.decode('utf8')[:-1]
        file = self.socket.recv(length)
        command.process_file(self, uuid, file)"""

    def disconnect(self):  # TODO: Handle socket error
        """Disconnects the client"""
        self.socket.close()
        self.connected = False
        if self in server.new_connections:
            server.new_connections.remove(self)
        if self in server.connected:
            server.connected.remove(self)
        self.socket = None
        print('User '+self.username+' disconnected')


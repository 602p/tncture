from ...ax25.frame import *
from ...ax25.abm import *
from ...transport.kiss import *
import sys, time
import threading


def run_ui(session):
	stream_outgoing = None

	def input_handler():
		nonlocal stream_outgoing
		while 1:
			data = input().encode('ascii') + b'\r'
			stream_outgoing = data

	threading.Thread(target=input_handler, daemon=True).start()

	# connected = False

	print(f"[client] Dialing {session.state.config.mycall} -> {session.state.config.theircall} via KISS:localhost:8001")

	while 1:
		session.poll()
		if stream_outgoing:
			session.write(stream_outgoing)
			stream_outgoing = None
		r = session.read()
		if r:
			print("-->", r)
		time.sleep(0.05)
		# if session.stream_incoming:
		# 	print(session.stream_incoming.decode('ascii', 'ignore').replace('\r', '\n'), end='', flush=True)
		# 	session.stream_incoming = b''

		# time.sleep(0.1)
		# if session.state == AX25ConnectedModeConnection.States.DISCONNECTED:
		# 	print("[client] Disconnected.")
		# 	break

		# if not connected and session.state == AX25ConnectedModeConnection.States.CONNECTED:
		# 	print("[client] Connected.")
		# 	connected = True
	sys.exit(0)

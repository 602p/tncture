from ax25_frame import *
from ax25_abm import *
from kiss import *
import sys, time
import threading


if __name__ == '__main__':
	if len(sys.argv) < 3:
		print("Usage: log_packets.py mycall theircall")
		sys.exit(1)

	mycall = AX25Address.parse(sys.argv[1])
	theircall = AX25Address.parse(sys.argv[2])

	conn = TCPKISSConnection('localhost', 8001)
	p = KISSPort(conn, 0, debug=0)
	session = AX25ConnectedModeConnection(p, mycall, theircall, debug=0)

	def input_handler():
		# print("input_handler() running")
		while 1:
			data = input().encode('ascii') + b'\r'
			# print("input_handler() submit", data)
			session.stream_outgoing += data

	threading.Thread(target=input_handler, daemon=True).start()

	connected = False

	print(f"[client] Dialing {mycall} -> {theircall} via KISS:localhost:8001")

	while 1:
		session.poll()
		if session.stream_incoming:
			print(session.stream_incoming.decode('ascii', 'ignore').replace('\r', '\n'), end='', flush=True)
			session.stream_incoming = b''
		# time.sleep(0.1)
		if session.state == AX25ConnectedModeConnection.States.DISCONNECTED:
			print("[client] Disconnected.")
			break

		if not connected and session.state == AX25ConnectedModeConnection.States.CONNECTED:
			print("[client] Connected.")
			connected = True

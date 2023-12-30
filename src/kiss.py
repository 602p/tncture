import socket, time
from ax25_frame import *

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD

class TCPKISSConnection:
	def __init__(self, address, port):
		self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.s.connect((address, port))
		self.s.setblocking(0)

		self.rx_byte_buffer = b''
		self.rx_frame_buffers = [[] for x in range(16)]

	@staticmethod
	def pack_slip_frame(frame):
		output = []
		for b in frame:
			if b == FEND:
				output.extend([FESC, TFEND])
			elif b == FESC:
				output.extend([FESC, TFESC])
			else:
				output.append(b)
		return output

	@staticmethod
	def unpack_slip_frame(frame):
		frame = list(frame)
		output = []
		while frame:
			b = frame.pop(0)
			if b == FESC:
				n = frame.pop(0)
				if n == TFESC:
					output.append(FESC)
				elif n == TFEND:
					output.append(FEND)
				else:
					raise ValueError("Bad TFESC sequence in KISS frame")
			else:
				output.append(b)
		return bytes(output)

	def send_raw_kiss_frame(self, port_index, command_code, data):
		command_byte = port_index << 4 | command_code
		frame = bytes([
			FEND,
			command_byte,
			*self.pack_slip_frame(data),
			FEND
		])

		self.s.sendall(frame)

	def send_data_frame(self, port_index, data):
		return self.send_raw_kiss_frame(0, 0, data)

	def recieve_raw_kiss_frame(self, port):
		try:
			while True:
				self.rx_byte_buffer += self.s.recv(1024)
		except BlockingIOError:
			pass

		while True:
			if self.rx_byte_buffer:
				assert self.rx_byte_buffer[0] == FEND

				end = self.rx_byte_buffer[1:].find(FEND)
				if end == -1:
					break

				frame = self.rx_byte_buffer[:end+2]
				self.rx_byte_buffer = self.rx_byte_buffer[end+2:]

				assert frame[0] == FEND, "Malformed KISS frame: "+repr(frame)
				assert frame[-1] == FEND, "Malformed KISS frame: "+repr(frame)

				frame = self.unpack_slip_frame(frame[1:-1])

				port = frame[0] >> 4
				self.rx_frame_buffers[port].append(frame)
			else:
				break

		if self.rx_frame_buffers[port]:
			return self.rx_frame_buffers[port].pop(0)
		else:
			return None

	def recieve_data_frame(self, port):
		frame = self.recieve_raw_kiss_frame(port)
		if not frame:
			return None

		assert (frame[0] & 0b1111) == 0, "KISS frame not data"

		return frame[1:]

class KISSPort:
	def __init__(self, conn, port, debug=False):
		self.conn = conn
		self.port = port
		self.debug = debug
		self.last_sent = None
		self.debug_fd = open("kiss_debug.txt", 'a')

	def send_data_frame(self, frame):
		self.debug_fd.write(str(time.time()) + ", send, " + repr(frame) + "\n")
		self.debug_fd.flush()
		if self.debug:
			print("KISSPort: send: ", parse_ax25_frame(frame, 8))
		self.conn.send_data_frame(self.port, frame)
		self.last_sent = frame

	def recieve_data_frame(self):
		frame = self.conn.recieve_data_frame(self.port)
		if frame:
			self.debug_fd.write(str(time.time()) + ", recv, " + repr(frame) + "\n")
			self.debug_fd.flush()
		if self.debug and frame and frame != self.last_sent:
			print("KISSPort: recv: ", parse_ax25_frame(frame, 8))
		return frame

class DummyKISSPort:
	def __init__(self):
		self.incoming_buf = []
		self.outgoing_buf = []

	def send_data_frame(self, frame):
		self.outgoing_buf.append(frame)

	def recieve_data_frame(self):
		if self.incoming_buf:
			return self.incoming_buf.pop(0)

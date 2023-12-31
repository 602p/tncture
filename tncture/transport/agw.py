from dataclasses import dataclass
from enum import Enum
import struct, socket, time

@dataclass
class RawAGWFrame:
	port: int
	datakind: int
	pid: int
	callfrom: str
	callto: str
	data: bytes

	HEADER_FORMAT = 'BxxxBxBx10s10sIxxxx'
	HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

	def to_buffer(self):
		return struct.pack(self.HEADER_FORMAT,
			self.port,
			ord(self.datakind),
			self.pid,
			self.callfrom.encode('ascii'),
			self.callto.encode('ascii'),
			len(self.data)) \
		+ self.data

	@classmethod
	def from_buffer(cls, buffer):
		header = buffer[:cls.HEADER_SIZE]
		data = buffer[cls.HEADER_SIZE:]
		*fields, datalen = struct.unpack(cls.HEADER_FORMAT, header)
		assert len(data) == datalen
		r = cls(*fields, data)
		r.datakind = chr(r.datakind)
		return r

	@classmethod
	def peek_size(cls, buffer):
		*_, datalen = struct.unpack(cls.HEADER_FORMAT, buffer)
		return datalen

@dataclass
class AGWReqFrame:
	port: int

	def __init_subclass__(cls, /, datakind, **k):
		super().__init_subclass__(**k)
		cls.datakind = datakind

	def to_raw(self):
		raw = RawAGWFrame(self.port, self.datakind, 0x00, '', '', b'')
		self.mod_raw(raw)
		return raw

	def mod_raw(self, raw):
		pass

@dataclass
class AGWRespFrame:
	port: int

	TYPES = {}

	def __init_subclass__(cls, /, datakind, **k):
		super().__init_subclass__(**k)
		AGWRespFrame.TYPES[datakind] = cls

	@staticmethod
	def parse(raw):
		cls = AGWRespFrame.TYPES.get(raw.datakind)
		if not cls:
			print("WARN: Unknown datakind: ", raw.datakind)
			return None
		return cls(raw.port, *cls.parse_members(raw))

@dataclass
class AGWReq_Version(AGWReqFrame, datakind='R'):
	pass

@dataclass
class AGWResp_Version(AGWRespFrame, datakind='R'):
	major: int
	minor: int

	@classmethod
	def parse_members(cls, raw):
		assert len(raw.data) == 8
		major = raw.data[0] + (raw.data[1] << 8)
		minor = raw.data[4] + (raw.data[5] << 8)

		return (major, minor)

@dataclass
class AGWReq_PortsInfo(AGWReqFrame, datakind='G'):
	pass

@dataclass
class AGWResp_PortsInfo(AGWRespFrame, datakind='G'):
	ports: list[str]

	@classmethod
	def parse_members(cls, raw):
		count, *items = raw.data.decode('ascii').split(';')
		return (items[:int(count)],)

@dataclass
class AGWReq_PortInfo(AGWReqFrame, datakind='g'):
	pass

@dataclass
class AGWResp_PortInfo(AGWRespFrame, datakind='g'):
	baudrate: int
	traffic_level: int
	tx_delay: int
	tx_tail: int
	persist: int
	slottime: int
	maxframe: int
	active_conns: int
	howmanybytes: int

	FORMAT = 'BBBBBBBBI'

	@classmethod
	def parse_members(cls, raw):
		return struct.unpack(cls.FORMAT, raw.data)

@dataclass
class AGWReq_EnableMonitoring(AGWReqFrame, datakind='m'):
	pass

@dataclass
class AGWReq_EnableRawMonitoring(AGWReqFrame, datakind='k'):
	pass

@dataclass
class AGWResp_MonitoredRawFrame(AGWRespFrame, datakind='K'):
	flag_port: int
	frame: bytes

	@classmethod
	def parse_members(cls, raw):
		return (raw.data[0], raw.data[1:])

@dataclass
class AGWResp_MonitoredOwnFrame(AGWRespFrame, datakind='T'):
	frame: bytes

	@classmethod
	def parse_members(cls, raw):
		return (raw.data,)

@dataclass
class AGWResp_MonitoredIFrame(AGWRespFrame, datakind='I'):
	content: bytes

	@classmethod
	def parse_members(cls, raw):
		return (raw.data,)

@dataclass
class AGWResp_MonitoredSFrame(AGWRespFrame, datakind='S'):
	content: bytes

	@classmethod
	def parse_members(cls, raw):
		return (raw.data,)

@dataclass
class AGWResp_MonitoredUFrame(AGWRespFrame, datakind='U'):
	content: bytes

	@classmethod
	def parse_members(cls, raw):
		return (raw.data,)

@dataclass
class AGWReq_EnableMonitorGPIO(AGWReqFrame, datakind='1'):
	pass

class GPIOSignal(Enum):
	PTT = 0
	DCD = 1
	UNKNOWN = 2

@dataclass
class AGWResp_MonitoredGPIO(AGWRespFrame, datakind='1'):
	signal: GPIOSignal
	value: int

	@classmethod
	def parse_members(cls, raw):
		return (GPIOSignal(raw.data[0]), bool(raw.data[1]))

class AGWTCPConnection:
	def __init__(self, address, port):
		self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.s.connect((address, port))
		self.s.setblocking(0)

		self.rx_byte_buffer = b''
		self.rx_frame_buffer = []

	def send_raw_agw_frame(self, frame):
		b = frame.to_buffer()
		self.s.sendall(b)

	def send_agw_frame(self, frame):
		self.send_raw_agw_frame(frame.to_raw())

	def recv_raw_agw_frame(self):
		try:
			while True:
				self.rx_byte_buffer += self.s.recv(1024)
		except BlockingIOError:
			pass

		while True:
			if len(self.rx_byte_buffer) >= RawAGWFrame.HEADER_SIZE:
				header = self.rx_byte_buffer[:RawAGWFrame.HEADER_SIZE]
				total_size = RawAGWFrame.HEADER_SIZE + RawAGWFrame.peek_size(header)
				if len(self.rx_byte_buffer) >= total_size:
					buffer = self.rx_byte_buffer[:total_size]
					self.rx_byte_buffer = self.rx_byte_buffer[total_size:]
					self.rx_frame_buffer.append(RawAGWFrame.from_buffer(buffer))
					continue
			break

		if self.rx_frame_buffer:
			return self.rx_frame_buffer.pop(0)

	def recv_agw_frame(self):
		f = self.recv_raw_agw_frame()
		if f:
			return AGWRespFrame.parse(f)

	def recv_agw_frame_blocking(self):
		f = None
		while f is None:
			f = self.recv_agw_frame()
		return f

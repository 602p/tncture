from dataclasses import dataclass
from enum import Enum

@dataclass
class AX25Address:
	callsign: str
	ssid: int
	rr: int = 0b11

	def __str__(self):
		return f"{self.callsign}-{self.ssid}"

	def same_station(self, other):
		return self.callsign == other.callsign and self.ssid == other.ssid

	@classmethod
	def parse(cls, s):
		if '-' in s:
			call, ssid = s.split('-')
			ssid = int(ssid)
			return cls(call, ssid)
		else:
			return cls(s, 0)

@dataclass
class AX25SourceAddress(AX25Address):
	c: bool = 0

	@property
	def bit(self):
		return self.c

@dataclass
class AX25DestinationAddress(AX25Address):
	c: bool = 0

	@property
	def bit(self):
		return self.c

@dataclass
class AX25RepeaterAddress(AX25Address):
	h: bool = 0

	@property
	def bit(self):
		return self.h

@dataclass
class AX25Control:
	mod128mode: bool

@dataclass
class AX25IControl:
	ns: int
	nr: int
	pf: bool

	def __str__(self):
		return f"I: N(S)={self.ns}, N(R)={self.nr}, PF={int(self.pf)}"

class SFrameTypes(Enum):
	RR   = 0b00
	RNR  = 0b01
	REJ  = 0b10
	SREJ = 0b11

@dataclass
class AX25SControl:
	ss: SFrameTypes
	nr: int
	pf: bool

	def __str__(self):
		return f"S: {self.ss.name}, N(R)={self.nr}, PF={int(self.pf)}"

class UFrameTypes(Enum):
	SABME = 0b01111
	SABM  = 0b00111
	DISC  = 0b01000
	DM    = 0b00011
	UA    = 0b01100
	FRMR  = 0b10001
	UI    = 0b00000
	XID   = 0b10111
	TEST  = 0b11100

@dataclass
class AX25UControl:
	mmmmm: UFrameTypes
	pf: bool

	def __str__(self):
		return f"U: {self.mmmmm.name}, PF={int(self.pf)}"

@dataclass
class AX25Frame:
	source: AX25SourceAddress
	dest: AX25DestinationAddress
	repeaters: list[AX25RepeaterAddress]
	control: AX25Control
	pid: list[int] = ()
	data: bytes = b''

	@property
	def frametype(self):
		return {
			AX25IControl: 'I',
			AX25UControl: 'U',
			AX25SControl: 'S'
		}[type(self.control)]

	def __str__(self):
		return f"{self.source} ({self.source.bit}) -> {self.dest} ({self.dest.bit}) [{','.join(map(str, self.repeaters))}]: {self.control} {self.data}"

def parse_ax25_address(address, type_):
	call = address[:6]
	call = ''.join([chr(x>>1) for x in call])
	call = call.rstrip(' ')

	last = address[6]
	c = last >> 7
	rr = (last >> 5) & 0b11
	ssid = (last >> 1) & 0b1111
	done = last & 0b1

	return type_(call, ssid, rr, c), done

def encode_ax25_address(address, done):
	call = address.callsign
	call = call + (' '*(6-len(call)))
	call = [ord(x)<<1 for x in call]

	if type(address) == AX25RepeaterAddress:
		b = address.h
	else:
		b = address.c

	last = (b << 7) | (address.rr << 5) | (address.ssid << 1) | done

	return bytes(call + [last])

def parse_ax25_control(control):
	mod128mode = len(control) == 2

	pf = (control[-1] >> 4) & 1
	
	if mod128mode:
		nr = control[0]>>1
	else:
		nr = control[-1]>>5

	if control[-1] & 1:
		if (control[-1] >> 1) & 1:
			mm = (control[-1] >> 2) & 0b11
			mmm = (control[-1] >> 5) & 0b111
			mmmmm = UFrameTypes((mmm << 2) | mm)
			return AX25UControl(mmmmm, pf)
		else:
			ss = SFrameTypes((control[-1] >> 2) & 0b11)
			return AX25SControl(ss, nr, pf)
	else:
		if mod128mode:
			ns = control[-1]>>1
		else:
			ns = (control[-1]>>1) & 0b111

		return AX25IControl(ns, nr, pf)

def encode_ax25_control(control, mod128mode):
	if type(control) == AX25UControl:
		mmm = control.mmmmm.value >> 2
		mm = control.mmmmm.value & 0b11
		assert mod128mode == 8
		return bytes([(mmm << 5) | (control.pf << 4) | (mm << 2) | 0b11])
	elif type(control) == AX25IControl:
		if mod128mode == 128:
			return bytes([(control.nr<<1) | control.pf, (control.ns << 1)])
		else:
			return bytes([(control.nr<<5) | (control.pf << 4) | (control.ns << 1)])
	else:
		if mod128mode == 128:
			return bytes([(control.nr<<1) | control.pf, (control.ss.value << 1) | 0b1])
		else:
			return bytes([(control.nr<<5) | (control.pf << 4) | (control.ss.value << 1) | 0b1])


def parse_ax25_frame(frame, mod128mode=None):
	try:
		def take_bytes(n):
			nonlocal frame
			r = frame[:n]
			frame = frame[n:]
			return r

		dest, _ = parse_ax25_address(take_bytes(7), AX25SourceAddress)
		source, done = parse_ax25_address(take_bytes(7), AX25DestinationAddress)
		repeaters = []

		while not done:
			rp, done = parse_ax25_address(take_bytes(7), AX25RepeaterAddress)
			repeaters.append(rp)

		if mod128mode == 128:
			control = parse_ax25_control(take_bytes(2))
		elif mod128mode == 8:
			control = parse_ax25_control(take_bytes(1))
		else:
			raise ValueError("Unknown mod128mode")

		if type(control) == AX25IControl:
			pid = [take_bytes(1)[0]]
			if pid[0] in [0b11111111, 0b00001000]:
				pid.append(take_bytes(1)[0])
		else:
			pid = []

		return AX25Frame(source, dest, repeaters, control, pid, frame)
	except ImportError as e:
		print("====FAILURE PARSING AX.25 FRAME====")
		print(">", repr(frame), mod128mode, "<")
		return None

def encode_ax25_frame(frame, mod128mode):
	buf = b''

	buf += encode_ax25_address(frame.dest, False)
	buf += encode_ax25_address(frame.source, not frame.repeaters)

	for i, r in enumerate(frame.repeaters):
		buf += encode_ax25_address(r, i == len(frame.repeaters)-1)

	buf += encode_ax25_control(frame.control, mod128mode)
	buf += bytes(frame.pid)
	buf += frame.data

	return buf

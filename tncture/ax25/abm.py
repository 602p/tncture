from enum import Enum
from dataclasses import dataclass
from .frame import *
import time

class ConnState(Enum):
	CONNECTING = 0    # Sending SABM(E)
	CONNECTED = 1     # Information Transfer
	DISCONNECTING = 2 # Sending DISC
	DISCONNECTED = 3  # Closed

def funcrec(cls):
	cls = dataclass(frozen=True)(cls)
	def _set(self, **keys):
		parms = []
		for k in cls.__dataclass_fields__.keys():
			if k in keys:
				parms.append(keys[k])
			else:
				parms.append(getattr(self, k))
		return cls(*parms)
	cls._set = _set
	return cls

@funcrec
class ABMTimer:
	name: str
	timeout: int
	started: int = None

	@property
	def running(self):
		return self.started is not None

	@property
	def expired(self):
		return self.running and (time.time() - self.started) > self.timeout

	@property
	def elapsed(self):
		return time.time() - self.started

	def start(self, bonus_time=0):
		return self._set(started=time.time() + bonus_time)

	def start_expired(self):
		return self.start(-9999)

	def stop(self):
		return self._set(started=None)

@funcrec
class ABMConfig:
	mycall: str
	theircall: str

	window_size: int = 8
	mtu: int = 200

	nonfinal_i_frame_burst_recieve_offset: int = 3
	# Adjustment for the burst receive timer for an I-frame with PF=0

	queue_visible: bool = False
	# i.e. is this over AGW and we can see outstanding queued frames and get 'T' updates when our
	# frames are transmitted

	dcd_capable: bool = False
	# i.e. is this over modded AGW and we can see DCD and PTT


@funcrec
class ABMInput_UserInput:
	data: bytes

@funcrec
class ABMInput_UserDisconnectRequest:
	pass

@funcrec
class ABMInput_UserReadyChange:
	ready: bool

@funcrec
class ABMInput_RXFrame:
	frame: AX25Frame

@funcrec
class ABMInput_TXFrame:
	pass

@funcrec
class ABMInput_DCDChange:
	state: bool

@funcrec
class ABMInput_PTTChange:
	state: bool

@funcrec
class ABMOutput_TXFrame:
	frame: AX25Frame

@funcrec
class ABMOutput_UserOutput:
	data: bytes

# TODO: Timer expiry

@funcrec
class ABMSMState:
	config: ABMConfig

	conn_state: ConnState = ConnState.CONNECTING

	vs: int = 0
	vr: int = 0
	va: int = 0

	keepalive_timer: ABMTimer = ABMTimer('keepalive', 30)
	retransmit_timer: ABMTimer = ABMTimer('retransmit', 10, -10) # Start immediately
	burst_recieve_timer: ABMTimer =  ABMTimer('burst_recieve', 3)

	outstanding_transmit_frame: bytes = None
	queued_transmit_bytes: bytes = b''

	def _base_frame(self, self_c, other_c, control, pid=[], data=b''):
		return AX25Frame(
			AX25SourceAddress(self.config.mycall.callsign, self.config.mycall.ssid, c=self_c),
			AX25DestinationAddress(self.config.theircall.callsign, self.config.theircall.ssid, c=other_c),
			[], # No repeaters
			control,
			pid,
			data
		)

	def _cmd(self, control, pid=[], data=b''):
		return ABMOutput_TXFrame(self._base_frame(0, 1, control, pid, data))

	def _resp(self, control, pid=[], data=b''):
		return ABMOutput_TXFrame(self._base_frame(1, 0, control, pid, data))

	@property
	def is_CONNECTING(self):
		return self.conn_state == ConnState.CONNECTING
	@property
	def is_CONNECTED(self):
		return self.conn_state == ConnState.CONNECTED
	@property
	def is_DISCONNECTING(self):
		return self.conn_state == ConnState.DISCONNECTING
	@property
	def is_DISCONNECTED(self):
		return self.conn_state == ConnState.DISCONNECTED

	def bail(self, text, *stuff):
		raise ValueError(text, self, *stuff)

	def mod(self, n):
		return n % self.config.window_size

	def do_nothing(self, why=None):
		return self, [], why

	def frmr(self, why=None):
		return self, [self._resp(AX25UControl(UFrameTypes.FRMR, pf=1))], why

	def disconnect(self, why, send_ua=False):
		return self._set(
			conn_state=ConnState.DISCONNECTED,
			retransmit_timer=self.retransmit_timer.stop(),
			keepalive_timer=self.keepalive_timer.stop(),
			burst_recieve_timer=self.burst_recieve_timer.stop()
		), ([self._resp(AX25UControl(UFrameTypes.UA, pf=1))] if send_ua else []), 'Disconnect'+('+UA' if send_ua else '')+": "+why

	def transmit_current_pending_frame(self, why):
		return self._set(retransmit_timer=self.retransmit_timer.start()), \
				[self._cmd(AX25IControl(ns=self.mod(self.vs - 1), nr=self.vr, pf=1), [0xf0], self.outstanding_transmit_frame)], why

	def step(self, new):
		newt = type(new)

		if newt is ABMInput_UserInput:
			return self._set(queued_transmit_bytes=self.queued_transmit_bytes + new.data), [], None

		if newt is ABMInput_UserDisconnectRequest:
			return self._set(
				conn_state = ConnState.DISCONNECTING,
				retransmit_timer = self.retransmit_timer.start_expired(),
				keepalive_timer = self.keepalive_timer.stop(),
				burst_recieve_timer = self.burst_recieve_timer.stop()
			), [], None

		if newt is ABMInput_RXFrame:
			self = self._set(keepalive_timer=self.keepalive_timer.start())

			if type(new.frame.control) is AX25IControl:
				return self.step_iframe(new.frame)
			elif type(new.frame.control) is AX25SControl:
				return self.step_sframe(new.frame)
			else: # U-frame
				return self.step_uframe(new.frame)

		if new:
			self.bail("Unhandled input", new)
		# Now we know that we are "steady-state" and free to process

		if self.is_CONNECTING and self.retransmit_timer.expired:
			return self._set(retransmit_timer=self.retransmit_timer.start()), [self._cmd(AX25UControl(UFrameTypes.SABM, pf=1))], 're/transmit SABM'

		if self.is_DISCONNECTING and self.retransmit_timer.expired:
			return self._set(retransmit_timer=self.retransmit_timer.start()), [self._cmd(AX25UControl(UFrameTypes.DISC, pf=1))], 'Send DISC'

		if self.is_CONNECTED:
			if self.retransmit_timer.expired:
				return self.transmit_current_pending_frame("Retransmit")

			if self.burst_recieve_timer.expired:
				return self._set(burst_recieve_timer=self.burst_recieve_timer.stop()), [self._resp(AX25SControl(SFrameTypes.RR, nr=self.vr, pf=1))], 'Send ACK'
		
			if self.keepalive_timer.expired:
				return self._set(keepalive_timer=self.keepalive_timer.start()), [self._cmd(AX25SControl(SFrameTypes.RR, nr=self.vr, pf=1))], 'Send keep-alive'

			if self.queued_transmit_bytes:
				if self.vs == self.va:
					data = self.queued_transmit_bytes[:self.config.mtu]
					new_qtb = self.queued_transmit_bytes[self.config.mtu:]
					self = self._set(
						outstanding_transmit_frame = data,
						queued_transmit_bytes = new_qtb,
						burst_recieve_timer = self.burst_recieve_timer.stop(),
						vs = self.mod(self.vs + 1)
					)
					return self.transmit_current_pending_frame(data)
				else:
					return self.do_nothing("Have outstanding frame, can't TX")

		return self.do_nothing(None)

	def step_iframe(self, new):
		if self.is_CONNECTED:
			self = self._set(va = new.control.nr)
			if new.control.ns == self.vr:
				return self._set(
					vr=self.mod(new.control.ns + 1),
					burst_recieve_timer=self.burst_recieve_timer.start(self.config.nonfinal_i_frame_burst_recieve_offset if new.control.pf==0 else 0)
				), [ABMOutput_UserOutput(new.data)], "Accept I-frame"
			else:
				if new.control.pf:
					return self._set(
						burst_recieve_timer=self.burst_recieve_timer.stop()
					), [self._resp(AX25SControl(SFrameTypes.REJ, nr=self.vr, pf=1))], "Reject out-of-order I-frame"
				else:
					return self.do_nothing("Ignoring out-of-order I-frame with PF=0")
		else:
			return self.do_nothing("I-frame while not CONNECTED, ignore: " + repr(new))

	def step_sframe(self, new):
		if self.is_CONNECTED:
			if new.control.ss == SFrameTypes.RR:
				if new.dest.c:
					# Polling acknowledgement
					self = self._set(
						burst_recieve_timer=self.burst_recieve_timer.start()
					)

				if new.control.nr == self.vs:
					return self._set(
						va = new.control.nr,
						retransmit_timer = self.retransmit_timer.stop(),
						outstanding_transmit_frame = None
					), [], ("Polling ACK request" if new.dest.c else "My I-frame was ACKed")
				else:
					return self.do_nothing("ACK for past frame")
				
			elif new.control.ss == SFrameTypes.RNR:
				return self.do_nothing("Don't support RNR yet")
			elif new.control.ss == SFrameTypes.REJ:
				if self.outstanding_transmit_frame:
					return self.transmit_current_pending_frame("REJ for pending frame")
				else:
					return self.do_nothing("REJ for ACKed frame, ignore")
			else:
				return self.frmr("Unknown SS")
		else:
			return self.do_nothing("Ignore S-frame when not CONNECTED: " + repr(new))

	def step_uframe(self, new):
		typ = new.control.mmmmm
		pf = new.control.pf

		if self.is_CONNECTING:
			if typ == UFrameTypes.UA:
				return self._set(
					conn_state=ConnState.CONNECTED,
					retransmit_timer=self.retransmit_timer.stop(),
					keepalive_timer=self.keepalive_timer.start()
				), [], 'Got UA, now CONNECTED'
			elif typ == UFrameTypes.DM:
				return self.disconnect('Got DM while CONNECTING')

			self.bail('Unhandled U while CONNECTING', new)

		elif self.is_DISCONNECTING:
			if typ == UFrameTypes.UA:
				return self.disconnect("Got UA while DISCONNECTING, done")

		if typ == UFrameTypes.DISC:
			return self.disconnect("Got DISC", send_ua=True)

		return self.do_nothing("Ignore unknown U frame: " + repr(new))



class AX25ConnectedModeConnection:
	def __init__(self, port, mycall, theircall):
		self.port = port
		self.state = ABMSMState(ABMConfig(mycall, theircall))

		self.output_buffer = b''

		self.debug_print = lambda *a, **k: None
		self.enqueued_inputs = []

	def _run_to_completion(self):
		while True:
			last_state = self.state
			i = None
			if self.enqueued_inputs:
				i = self.enqueued_inputs.pop(0)
			self.state, outgoing, message = self.state.step(i)
			if message:
				self.debug_print(message)
			self._handle_outgoing(outgoing)
			if last_state == self.state:
				return

	def _handle_outgoing(self, outgoing):
		for row in outgoing:
			if type(row) is ABMOutput_TXFrame:
				self.debug_print("[output] TX frame: ", row.frame)
				self.port.send_data_frame(encode_ax25_frame(row.frame, 8))
			elif type(row) is ABMOutput_UserOutput:
				self.debug_print("[output] User output: ", row.data)
				self.output_buffer += row.data
			else:
				raise ValueError("Unknown output from statemachine:", row)

	def handle_rx_frame(self, rx_frame):
		f = parse_ax25_frame(rx_frame, 8)
		if not f.dest.same_station(self.state.config.mycall):
			return

		self.enqueued_inputs.append(ABMInput_RXFrame(f))

	def disconnect(self):
		self.enqueued_inputs.append(ABMInput_UserDisconnectRequest())

	def write(self, data):
		self.enqueued_inputs.append(ABMInput_UserInput(data))

	def poll(self):
		f = self.port.recieve_data_frame()
		if f:
			self.handle_rx_frame(f)
		
		self._run_to_completion()

	def read(self):
		r = self.output_buffer
		self.output_buffer = b''
		return r
	
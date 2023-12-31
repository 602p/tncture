from enum import Enum
from .frame import *
import time

class Timer:
	def __init__(self, name, timeout):
		self.name = name
		self.timeout = timeout
		self.started = None

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
		self.started = time.time() + bonus_time

	def stop(self):
		self.started = None

class AX25ConnectedModeConnection:
	class States(Enum):
		CONNECTING = 0    # Sending SABM(E)
		CONNECTED = 1     # Information Transfer
		DISCONNECTING = 2 # Sending DISC
		DISCONNECTED = 3  # Closed

	def __init__(self, port, mycall, theircall):
		self.mycall = mycall
		self.theircall = theircall
		self.port = port

		self.stream_outgoing = b''
		self.stream_incoming = b''

		self.vs = 0 # Send State Variable
		#self.ns = 0 # Send Sequence Number
		self.vr = 0 # Receive State Variable
		#self.nr = 0 # Received Sequence Number
		self.va = 0 # Acknowledge State Variable

		self.window_size = 8

		self.state = self.States.CONNECTING

		self.keepalive_timer = Timer('keepalive', 30)
		self.retransmit_timer = Timer('retransmit', 10)
		self.burst_recieve_timer = Timer('burst_recieve', 3)

		self.mtu = 200
		self.pending_ack_frame = None

		self.faultinject = False

		self._base_cmd = self._base_frame(0, 1)
		self._base_rsp = self._base_frame(1, 0)

		self.debug_print = lambda *a, **k: None

	def _base_frame(self, self_c, other_c):
		return (
			AX25SourceAddress(self.mycall.callsign, self.mycall.ssid, c=self_c),
			AX25DestinationAddress(self.theircall.callsign, self.theircall.ssid, c=other_c)
		)

	def send_frame(self, frame):
		if self.faultinject and frame.data==b'B\r':
			self.faultinject = False
			self.debug_print("FAULT-INJECT NO RX")
			return
		self.debug_print("AX25ConnectedModeConnection: send:", frame)
		return self.port.send_data_frame(encode_ax25_frame(frame, 8))

	def initiate_disconnection(self):
		self.state = self.States.DISCONNECTING
		self.keepalive_timer.stop()
		self.burst_recieve_timer.stop()
		self.retransmit_timer.start(-1000) # Send immediately

	def disconnect(self):
		self.state = self.States.DISCONNECTED
		self.retransmit_timer.stop()
		self.burst_recieve_timer.stop()
		self.keepalive_timer.stop()

	def send_UA(self):
		self.send_frame(AX25Frame(
			*self._base_rsp, [],
			AX25UControl(UFrameTypes.UA, pf=1)
		))

	def poll(self):
		dbg = self.debug_print

		newmsg = self.port.recieve_data_frame()
		if newmsg:
			newmsg = parse_ax25_frame(newmsg, 8)
			if newmsg:
				if (not newmsg.dest.same_station(self.mycall)) or (not newmsg.source.same_station(self.theircall)):
					newmsg = None

		if self.state == self.States.DISCONNECTED:
			if newmsg:
				if newmsg.control.mmmmm == UFrameTypes.DISC:
					dbg("Got DISC while DISCONNECTED, send UA again")
					self.send_UA()
				else:
					dbg("??? frame while DISCONNECTED")
			return

		if newmsg:
			dbg("AX25ConnectedModeConnection: recv:", newmsg)
			self.keepalive_timer.start()

		if self.state == self.States.CONNECTING and not self.retransmit_timer.running:
			self.retransmit_timer.start(-1000) # Start

		if newmsg:
			if newmsg.frametype == 'U':
				if self.state == self.States.CONNECTING:
					if newmsg.control.mmmmm == UFrameTypes.UA:
						dbg("Got UA, going CONNECTING -> CONNECTED")
						self.state = self.States.CONNECTED
						self.retransmit_timer.stop()
						self.keepalive_timer.start()

					if newmsg.control.mmmmm == UFrameTypes.DM:
						dbg("Got DM, going CONNECTING -> DISCONNECTED")
						self.disconnect()

				if self.state == self.States.DISCONNECTING:
					if newmsg.control.mmmmm == UFrameTypes.UA:
						dbg("Got UA, going CONNECTED -> DISCONNECTED")
						self.disconnect()
				
				if self.state == self.States.CONNECTED:
					if newmsg.control.mmmmm == UFrameTypes.DISC:
						dbg("Got DISC, going CONNECTED -> DISCONNECTED")
						self.disconnect()
						self.send_UA()

			if self.state == self.States.CONNECTED and newmsg.frametype == 'I':
				self.va = newmsg.control.nr
				if newmsg.control.ns == self.vr:
					dbg("Accept I frame: ", newmsg.data)
					self.stream_incoming += newmsg.data
					self.vr = (newmsg.control.ns + 1) % self.window_size
					self.vr_needs_sending = True
					self.burst_recieve_timer.start(5 if newmsg.control.pf == 0 else 0)
				else:
					# out of order
					# TODO: Restricts to non-selective reject
					if newmsg.control.pf:
						dbg("Out of order I-frame, REJ")
						self.send_frame(AX25Frame(
							*self._base_rsp, [],
							AX25SControl(ss=SFrameTypes.REJ, nr=self.vr, pf=1)
						))
						self.burst_recieve_timer.stop() # REJ includes ACK
					else:
						dbg("Got out of order I-frame with PF=0, ignoring for now")
			
			if self.state == self.States.CONNECTED and newmsg.frametype == 'S':
				if newmsg.control.ss == SFrameTypes.RR:
					self.va = newmsg.control.nr
					if newmsg.dest.c:
						dbg("Receive polling acknowledgement, reply")
						self.send_frame(AX25Frame(
							*self._base_rsp, [], #C/C bits backwards
							AX25SControl(ss=SFrameTypes.RR, nr=self.vr, pf=1)
						))
						self.burst_recieve_timer.stop()
					else:
						dbg("Receive normal acknowledgement")

					if self.va == self.vs:
						self.pending_ack_frame = None
						self.retransmit_timer.stop()
					else:
						dbg("Recieved ACK for past frame")
						pass # Should resend because this ack was for a past frame
					
				elif newmsg.control.ss == SFrameTypes.REJ:
					if self.pending_ack_frame:
						print("REJ for pending frame, resend")
						self.send_frame(AX25Frame(
							*self._base_cmd, [],
							AX25IControl(ns=self.vs, nr=self.vr, pf=1),
							[0xf0],
							self.pending_ack_frame
						))
						self.retransmit_timer.start()
					else:
						print("REJ for ACKed frame, ignore")

		newvr = (self.vs + 1) % self.window_size
		# TODO: Restricts to exactly one outstanding TX frame
		if self.state == self.States.CONNECTED and self.stream_outgoing:
			if self.vs == self.va:
				dbg("TX frame")
				frame = self.stream_outgoing[:self.mtu]
				self.stream_outgoing = self.stream_outgoing[self.mtu:]
				self.send_frame(AX25Frame(
					*self._base_cmd, [],
					AX25IControl(ns=self.vs, nr=self.vr, pf=1),
					[0xf0],
					frame
				))
				self.pending_ack_frame = frame
				self.vs = newvr
				self.burst_recieve_timer.stop()
				self.retransmit_timer.start()
				return
			else:
				dbg("Have outstanding data, can't TX")
		
		if self.state == self.States.CONNECTING and self.retransmit_timer.expired:
			dbg("Transmit SABM")
			self.send_frame(AX25Frame(
				*self._base_cmd, [],
				AX25UControl(UFrameTypes.SABM, pf=1)
			))
			self.retransmit_timer.start()
		elif self.state == self.States.CONNECTED:
			if self.pending_ack_frame and self.retransmit_timer.expired:
				dbg("Resend I-frame")
				self.send_frame(AX25Frame(
					*self._base_cmd, [],
					AX25IControl(ns=(self.vs - 1) % self.window_size, nr=self.vr, pf=1),
					[0xf0],
					self.pending_ack_frame
				))
				self.retransmit_timer.start()
			elif self.keepalive_timer.expired:
				# Keep-alive
				dbg("Send keep-alive")
				self.send_frame(AX25Frame(
					*self._base_cmd, [],
					AX25SControl(ss=SFrameTypes.RR, nr=self.vr, pf=1)
				))
				self.keepalive_timer.start()
		elif self.state == self.States.DISCONNECTING and self.retransmit_timer.expired:
			dbg("Transmit DISC")
			self.send_frame(AX25Frame(
				*self._base_cmd, [],
				AX25UControl(UFrameTypes.DISC, pf=1)
			))
			self.retransmit_timer.start()

		if (not newmsg) and self.burst_recieve_timer.expired:
			dbg("Send delayed RR")
			self.send_frame(AX25Frame(
				*self._base_rsp, [],
				AX25SControl(ss=SFrameTypes.RR, nr=self.vr, pf=1)
			))
			self.burst_recieve_timer.stop()
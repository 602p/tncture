from enum import Enum
from .frame import *
import time

class AX25ConnectedModeConnection:
	class States(Enum):
		CONNECTING = 0    # Sending SABM(E)
		CONNECTED = 1     # Information Transfer
		DISCONNECTING = 2 # Sending DISC
		DISCONNECTED = 3  # Closed

	def __init__(self, port, mycall, theircall, debug=0):
		self.mycall = mycall
		self.theircall = theircall
		self.port = port
		self.debug = debug

		self.stream_outgoing = b''
		self.stream_incoming = b''

		self.vs = 0 # Send State Variable
		#self.ns = 0 # Send Sequence Number
		self.vr = 0 # Receive State Variable
		self.vr_needs_sending = False
		#self.nr = 0 # Received Sequence Number
		self.va = 0 # Acknowledge State Variable

		self.window_size = 8

		self.state = self.States.CONNECTING

		self.retransmit_timeout = 15 #S. ~T1
		self.acknowledgement_wait_started = -1

		self.burst_recieve_timeout = 5 # Time to wait before sending RR
		self.burst_recieve_wait_started = -1

		self.mtu = 200
		self.pending_ack_frame = None

		self.faultinject = False

	def reset_acknowledgement_wait(self):
		self.acknowledgement_wait_started = time.time()

	def reset_burst_recieve_wait(self):
		self.burst_recieve_wait_started = time.time()

	def _base_frame(self, self_c, other_c):
		return (
			AX25SourceAddress(self.mycall.callsign, self.mycall.ssid, c=self_c),
			AX25DestinationAddress(self.theircall.callsign, self.theircall.ssid, c=other_c)
		)

	def send_frame(self, frame):
		if self.faultinject and frame.data==b'B\r':
			self.faultinject = False
			print("FAULT-INJECT NO RX")
			return
		if self.debug:
			print("AX25ConnectedModeConnection: send:", frame)
		return self.port.send_data_frame(encode_ax25_frame(frame, 8))

	def initiate_disconnection(self):
		self.state = self.States.DISCONNECTING
		self.acknowledgement_wait_started = -1

	def poll(self):
		def dbg(*a, **k):
			if self.debug:
				print(*a, **k)

		newmsg = self.port.recieve_data_frame()
		if newmsg:
			newmsg = parse_ax25_frame(newmsg, 8)
			if newmsg:
				if (not newmsg.dest.same_station(self.mycall)) or (not newmsg.source.same_station(self.theircall)):
					newmsg = None

		if self.state == self.States.DISCONNECTED:
			dbg("??? frame while DISCONNECTED")
			return

		if newmsg:
			dbg("AX25ConnectedModeConnection: recv:", newmsg)

		should_retry = (time.time() - self.acknowledgement_wait_started) > self.retransmit_timeout

		if newmsg:
			if newmsg.frametype == 'U':
				if self.state == self.States.CONNECTING:
					if newmsg.control.mmmmm == UFrameTypes.UA:
						dbg("Got UA, going CONNECTING -> CONNECTED")
						self.state = self.States.CONNECTED
						self.reset_acknowledgement_wait()

					if newmsg.control.mmmmm == UFrameTypes.DM:
						dbg("Got DM, going CONNECTING -> DISCONNECTED")
						self.state = self.States.DISCONNECTED

				if self.state == self.States.DISCONNECTING:
					if newmsg.control.mmmmm == UFrameTypes.UA:
						dbg("Got UA, going CONNECTED -> DISCONNECTED")
						self.state = self.States.DISCONNECTED
				
				if self.state == self.States.CONNECTED:
					if newmsg.control.mmmmm == UFrameTypes.DISC:
						dbg("Got DISC, going CONNECTED -> DISCONNECTED")
						self.state = self.States.DISCONNECTED
						self.reset_acknowledgement_wait()
						self.send_frame(AX25Frame(
							*self._base_frame(1, 0), [],
							AX25UControl(UFrameTypes.UA, pf=1)
						))

			if self.state == self.States.CONNECTED and newmsg.frametype == 'I':
				self.va = newmsg.control.nr
				if newmsg.control.ns == self.vr:
										
					dbg("Accept I frame: ", newmsg.data)
					self.stream_incoming += newmsg.data
					self.vr = (newmsg.control.ns + 1) % self.window_size
					self.vr_needs_sending = True
					self.reset_acknowledgement_wait()
					self.reset_burst_recieve_wait()
				else:
					# out of order
					# TODO: Restricts to non-selective reject
					dbg("Out of order I-frame, REJ")
					self.send_frame(AX25Frame(
						*self._base_frame(1, 0), [],
						AX25SControl(ss=SFrameTypes.REJ, nr=self.vr, pf=1)
					))
			
			if self.state == self.States.CONNECTED and newmsg.frametype == 'S':
				if newmsg.control.ss == SFrameTypes.RR:
					self.va = newmsg.control.nr
					if newmsg.dest.c:
						dbg("Receive polling acknowledgement, reply")
						self.send_frame(AX25Frame(
							*self._base_frame(1, 0), [], #C/C bits backwards
							AX25SControl(ss=SFrameTypes.RR, nr=self.vr, pf=1)
						))
						self.vr_needs_sending = False
					else:
						dbg("Receive normal acknowledgement")

					if self.va == self.vs:
						self.pending_ack_frame = None
						self.reset_acknowledgement_wait()
					else:
						dbg("Recieved ACK for past frame")
						pass # Should resend because this ack was for a past frame
					
				elif newmsg.control.ss == SFrameTypes.REJ:
					assert self.pending_ack_frame
					self.send_frame(AX25Frame(
						*self._base_frame(0, 1), [],
						AX25IControl(ns=self.vs, nr=self.vr, pf=1),
						[0xf0],
						self.pending_ack_frame
					))
					self.reset_acknowledgement_wait()

		newvr = (self.vs + 1) % self.window_size
		# TODO: Restricts to exactly one outstanding TX frame
		if self.state == self.States.CONNECTED and self.stream_outgoing:
			if self.vs == self.va:
				dbg("TX frame")
				frame = self.stream_outgoing[:self.mtu]
				self.stream_outgoing = self.stream_outgoing[self.mtu:]
				self.send_frame(AX25Frame(
					*self._base_frame(0, 1), [],
					AX25IControl(ns=self.vs, nr=self.vr, pf=1),
					[0xf0],
					frame
				))
				self.pending_ack_frame = frame
				self.vs = newvr
				self.vr_needs_sending = False
				self.reset_acknowledgement_wait()
				return
			else:
				dbg("Have outstanding data, can't TX")
		
		if self.state == self.States.CONNECTING and should_retry:
			dbg("Transmit SABM")
			self.send_frame(AX25Frame(
				*self._base_frame(0, 1), [],
				AX25UControl(UFrameTypes.SABM, pf=1)
			))
			self.reset_acknowledgement_wait()
		elif self.state == self.States.CONNECTED and should_retry:
			if self.pending_ack_frame:
				dbg("Resend I-frame")
				self.send_frame(AX25Frame(
					*self._base_frame(0, 1), [],
					AX25IControl(ns=(self.vs - 1) % self.window_size, nr=self.vr, pf=1),
					[0xf0],
					self.pending_ack_frame
				))
				self.reset_acknowledgement_wait()
			else:
				# Keep-alive
				dbg("Send keep-alive")
				self.send_frame(AX25Frame(
					*self._base_frame(1, 0), [],
					AX25SControl(ss=SFrameTypes.RR, nr=self.vr, pf=1)
				))
				self.reset_acknowledgement_wait()
		elif self.state == self.States.DISCONNECTING and should_retry:
			dbg("Transmit DISC")
			self.send_frame(AX25Frame(
				*self._base_frame(1, 0), [],
				AX25UControl(UFrameTypes.DISC, pf=1)
			))
			self.reset_acknowledgement_wait()

		if (not newmsg) and self.vr_needs_sending and ((time.time() - self.burst_recieve_wait_started) > self.burst_recieve_timeout):
			dbg("Send delayed RR")
			self.send_frame(AX25Frame(
				*self._base_frame(1, 0), [],
				AX25SControl(ss=SFrameTypes.RR, nr=self.vr, pf=1)
			))
			self.vr_needs_sending = False
			self.reset_acknowledgement_wait()
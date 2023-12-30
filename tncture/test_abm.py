from log_packets import load_sample_file
from ax25_frame import *
from ax25_abm import *
from kiss import *
import sys, time

def main():
	sample = load_sample_file(sys.argv[1])

	initial_t = sample[0][1]
	initial = parse_ax25_frame(sample[0][2], 8)

	p = DummyKISSPort()
	mycall = AX25Address(initial.source.callsign, initial.source.ssid)
	theircall = AX25Address(initial.dest.callsign, initial.dest.ssid)
	c = AX25ConnectedModeConnection(p, mycall=mycall, theircall=theircall)

	frame_counter = 0

	def get_next_frame():
		nonlocal frame_counter
		if not sample:
			print("DONE")
			sys.exit(0)
		while True:
			f = sample.pop(0)
			print_row(f)
			if f[0] == 'frame':
				parsed = parse_ax25_frame(f[2], 8)
				me = parsed.source.same_station(mycall)
				them = parsed.source.same_station(theircall)
				if not me and not them:
					continue
				return f
			elif f[0] == 'input':
				return f
			else:
				assert False, f

	def check(row, simulated):
		if row[2] != simulated:
			print("FAIL")
			print('actual:', parse_ax25_frame(row[2], 8))
			print('sim:', parse_ax25_frame(simulated, 8))
			raise ValueError

	def print_row(row):
		if row[0] == 'frame':
			if parse_ax25_frame(row[2], 8).source.same_station(mycall):
				print("Me:\t", f"{row[1]-initial_t:.2f}", parse_ax25_frame(row[2], 8))
			else:
				print("Them:\t", f"{row[1]-initial_t:.2f}", parse_ax25_frame(row[2], 8))
		else:
			print("Input:", row[2])

	if '--just-print' in sys.argv:
		while sample:
			get_next_frame()
		return

	while 1:
		print("\n"*3)
		c.poll()
		if p.outgoing_buf:
			simulated = p.outgoing_buf.pop(0)
			actual = get_next_frame()
			check(actual, simulated)
			continue # Poll again

		while True:
			actual = get_next_frame()
			if actual[0] == 'input':
				c.stream_outgoing += actual[2]
				continue

			assert parse_ax25_frame(actual[2], 8).source.same_station(theircall), "We missed TXing a frame"
			p.incoming_buf.append(actual[2])

			if sample and parse_ax25_frame(sample[0][2], 8).source.same_station(theircall):
				continue # Another RX
			else:
				break


if __name__ == '__main__':
	main()

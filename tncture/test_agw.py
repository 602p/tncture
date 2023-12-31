from .transport.agw import *
import time

c = AGWTCPConnection('localhost', 8000)

c.send_agw_frame(AGWReq_Version(0))
print(c.recv_agw_frame_blocking())

c.send_agw_frame(AGWReq_PortsInfo(0))
print(c.recv_agw_frame_blocking())

c.send_agw_frame(AGWReq_PortInfo(0))
print(c.recv_agw_frame_blocking())
# This response is garbage. See direwolf/src/server.c

c.send_agw_frame(AGWReq_EnableMonitoring(0))
c.send_agw_frame(AGWReq_EnableRawMonitoring(0))
c.send_agw_frame(AGWReq_EnableMonitorGPIO(0))
t0 = time.time()
while 1:
	f = c.recv_agw_frame_blocking()
	if type(f) in [AGWResp_MonitoredIFrame, AGWResp_MonitoredSFrame, AGWResp_MonitoredUFrame]:
		continue
		
	print(f"{time.time()-t0:.2f}", f)

# We get TXed information back as a 'T' frame
# We get remote information as a AGWResp_RawFrame (and as parsed if EnableMonitoring)
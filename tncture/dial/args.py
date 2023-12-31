import sys
from ..ax25.frame import *
from ..ax25.abm import *
from ..transport.kiss import *

def get_session(name):
    if len(sys.argv) < 3:
        print(f"Usage: {name} MYCALL[-X] THEIRCALL-X")
        sys.exit(1)

    mycall = AX25Address.parse(sys.argv[1])
    theircall = AX25Address.parse(sys.argv[2])

    if '--dummy' not in sys.argv:
        kiss = TCPKISSConnection('localhost', 8001)
    else:
        kiss = DummyKISSConnection()

    port = KISSPort(kiss, 0)
    session = AX25ConnectedModeConnection(port, mycall, theircall, debug=0)
    return session

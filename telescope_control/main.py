	

import PyGalil.galilInterface

import startGui						#GUI Handling

try:
	import pycallgraph
	pycallgraph.start_trace()
except ImportError:
	print "Do not have pycallgraph installed. Not tracing calls"


def go():



	import globalConf
	import ipCheck


	ipCheckWin = ipCheck.IpChecker(0)
	ipCheckWin.MainLoop()

	print "Startup check complete. IP = \"", globalConf.galilIP, "\""

	#def __init__(self, ip, port = 23, fakeGalil = False, poll = False, resetGalil = False):


	globalConf.gInt = PyGalil.galilInterface.GalilInterface(ip=globalConf.galilIP, fakeGalil=globalConf.fakeGalil, poll=False, resetGalil=False)

	print "opened galil. Connection:", globalConf.gInt
	#import time
	#time.sleep(1)

	startGui.main()


if __name__ == "__main__":
	go()

from .ax25.frame import *
from .ax25.abm import *
from .transport.agw import *
import sys, time

s = ABMTimer('T1', 15)
print(s.start())
print(s)
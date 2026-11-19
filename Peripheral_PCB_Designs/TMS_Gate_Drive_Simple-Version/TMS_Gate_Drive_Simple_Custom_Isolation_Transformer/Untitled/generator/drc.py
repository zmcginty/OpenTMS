import pcbnew,sys,re,collections
b=pcbnew.LoadBoard(sys.argv[1])
pcbnew.WriteDRCReport(b,sys.argv[2],pcbnew.EDA_UNITS_MILLIMETRES,True)
t=open(sys.argv[2]).read()
print(re.findall(r'\*\* Found \d+ [^*]+',t))
c=collections.Counter(re.findall(r'^\[(\w+)\]',t,re.M)); print(c)

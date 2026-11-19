import pcbnew, sys, collections, re
b = pcbnew.LoadBoard(sys.argv[1])
pcbnew.WriteDRCReport(b, sys.argv[2], pcbnew.EDA_UNITS_MILLIMETRES, True)
txt = open(sys.argv[2]).read()
print(collections.Counter(re.findall(r'^\[(\w+)\]', txt, re.M)).most_common())

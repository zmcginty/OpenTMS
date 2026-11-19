import pcbnew,sys
b=pcbnew.LoadBoard(sys.argv[1]); f=pcbnew.ZONE_FILLER(b); f.Fill(b.Zones()); pcbnew.SaveBoard(sys.argv[1],b); print('filled',b.Zones().size())

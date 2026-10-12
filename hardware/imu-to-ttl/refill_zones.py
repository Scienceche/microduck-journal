import pcbnew
b = pcbnew.LoadBoard('imu_to_ttl.kicad_pcb')
print('zones:', len(b.Zones()))
ok = pcbnew.ZONE_FILLER(b).Fill(b.Zones())
print('fill result:', ok)
pcbnew.SaveBoard('imu_to_ttl.kicad_pcb', b)
print('saved')

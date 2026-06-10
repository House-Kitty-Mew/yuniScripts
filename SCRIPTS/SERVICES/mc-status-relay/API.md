# MC Status Relay
Listens for Minescript packets on UDP 25566, caches latest biome/dim,
and responds to queries on UDP 25568 with format:
  BIOME DIM AGE_SECONDS

Commands (UDP 25568):
  status    – returns "minecraft:plains minecraft:overworld 3"
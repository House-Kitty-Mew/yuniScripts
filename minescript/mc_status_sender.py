# Minescript script: mc_status_sender.py
# For Minecraft 1.21.2+ and Minescript 5.0+
# Sends biome/dimension to YuniScripts engine via UDP.
import time
import socket
import minescript
from java import JavaClass

UDP_IP = "127.0.0.1"
UDP_PORT = 25566  # engine.ports.MINESCRIPT_SENDER_PORT (local define — minescript can't import engine)
SEND_INTERVAL = 5

seq = 0

def parse_resource_key(raw: str) -> str:
    """Extract last segment from ResourceKey.toString()."""
    try:
        inner = raw.split("[", 1)[1].rstrip("]")
        return inner.split(" / ")[-1].strip()
    except Exception:
        return raw

def get_biome_and_dimension():
    """Return (biome, dimension) as Python strings."""
    Minecraft = JavaClass("net.minecraft.client.Minecraft")
    mc = Minecraft.getInstance()
    if mc is None:
        return "Unknown", "Unknown"

    player = mc.player
    if player is None:
        return "Unknown", "Unknown"

    level = player.level()
    if level is None:
        return "Unknown", "Unknown"

    # Dimension
    dim_resource = level.dimension()
    dimension = parse_resource_key(str(dim_resource.toString()))

    # Biome
    block_pos = player.blockPosition()
    biome_holder = level.getBiome(block_pos)
    optional_key = biome_holder.unwrapKey()
    if bool(optional_key.isPresent()):
        resource_key = optional_key.get()
        biome_name = parse_resource_key(str(resource_key.toString()))
    else:
        biome_name = "Unknown"

    return biome_name, dimension

def send_status(sock, biome, dim):
    global seq
    msg = f"SEQ:{seq} BIOME:{biome} DIM:{dim}"
    sock.sendto(msg.encode("utf-8"), (UDP_IP, UDP_PORT))
    seq += 1

def main():
    global seq
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Notify player once
    minescript.echo("MC Status sender started. Connected to UDP.")

    try:
        while True:
            try:
                biome, dim = get_biome_and_dimension()
                send_status(sock, biome, dim)
            except Exception as e:
                minescript.echo(f"MC Status error: {e}")
            time.sleep(SEND_INTERVAL)
    finally:
        sock.close()

if __name__ == "__main__":
    main()

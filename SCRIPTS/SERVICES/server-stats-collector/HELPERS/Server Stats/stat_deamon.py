import socket
import subprocess
import psutil

# Create a UDP socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Bind the socket to the port
server_address = ('0.0.0.0', 5559)  # engine.ports.SERVER_STATS_PORT
sock.bind(server_address)

while True:
    try:
        data, address = sock.recvfrom(65500)
    except Exception:
        print('Something Happened when loading data from remote connection.')
        print(str(data))
        print(address)
        continue
    try:
        if str(data.decode('UTF-8')) == 'check':
            info = ''
            #grab temp of cpu
            #temp_m = subprocess.Popen("cat /sys/class/thermal/thermal_zone0/temp ", shell=True, stdout=subprocess.PIPE)
            #print(temp_m)
            f = open("C:/temp.txt", "r")
            temp_c = int(f.read())
            #data = temp_m.stdout.read()
            #temp_c = int(data) / 1000
            temp_f = (temp_c * 1.8) + 32
            info += str(temp_f) + '|*|'
            #grab ram info
            ram = psutil.virtual_memory()
            ram_total = ram[0] / 1024 / 1024
            ram_free = ram[1] / 1024 / 1024
            ram_percent = ram[2]
            info += str(ram_total) + '|*|' + str(ram_free) + '|*|' + str(ram_percent)
            #get cpu's current usage
            cpus_use = psutil.cpu_percent(interval=1, percpu=True)
            info += '|*|' + str(cpus_use)
            #get current disk space used
            disk_raw = psutil.disk_usage('/')
            disk = str(disk_raw[3])
            info += '|*|' + disk
            #get current network usage
            network_usage = psutil.net_io_counters()
            network_usage_sent = str(network_usage[0])
            network_usage_recv = str(network_usage[1])
            info += '|*|' + network_usage_sent + '|*|' + network_usage_recv
            #get all running proccess's
            proc_list = ''

            white_list = ['java.exe','EcoServer.exe','DedicatedServerLauncher1301.exe','ConanSandboxServer.exe','ConanSandboxServer-Win64-Test.exe','Torch.Server.exe','7DaysToDieServer.exe']
            repeat = 0
            for proc in psutil.process_iter(attrs=['pid', 'name', 'username']):
                name = proc.name()
                if(name in white_list):
                    if name == "java.exe":
                        if repeat == 0:
                            repeat = 1
                        elif repeat == 1:
                            continue
                        name = 'Lil\'topia Server'
                    username = proc.username()
                    proc_list += name + ' ' + username + ' \n'
            info += '|*|' + proc_list
            #send info back to client.
            sock.sendto(info.encode('UTF-8'), address)
        else:
            print('Sent wrong data: ' + str(data.decode('UTF-8')))
    except Exception as ex:
        print('Decoding or proccessing server information malfuctioned, skipping to next run. \n' + ex)
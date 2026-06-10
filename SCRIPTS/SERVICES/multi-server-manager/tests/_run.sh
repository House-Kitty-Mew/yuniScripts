#!/bin/bash
cd /home/deck/Documents/dev-yuniScripts/SCRIPTS/SERVICES/multi-server-manager
python3 -m unittest tests.test_admin_cli -v > /tmp/admin_cli_test_output.txt 2>&1
echo "DONE: exit code $?"

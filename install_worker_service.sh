#!/bin/bash
set -e
sudo cp /home/euclid/Documents/proj/realtor/realtor-worker.service /etc/systemd/system/realtor-worker.service
sudo systemctl daemon-reload
sudo systemctl enable realtor-worker.service
sudo systemctl start realtor-worker.service
sleep 3
sudo systemctl status realtor-worker.service --no-pager

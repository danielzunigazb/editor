#!/bin/bash
# One-time setup for the MCP server (Ubuntu 24.04). The apt binding only exists for the system Python 3.12.
set -e
sudo apt-get update
sudo apt-get install -y melt python3-mlt ffmpeg xvfb fonts-dejavu-core
/usr/bin/python3.12 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt

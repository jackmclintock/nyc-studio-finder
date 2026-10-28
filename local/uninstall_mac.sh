#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.nycstudiofinder.renthop.plist"
launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST"; echo "RentHop home runner schedule removed."

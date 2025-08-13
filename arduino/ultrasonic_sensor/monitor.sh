#!/bin/sh

arduino-cli monitor -p /dev/ttyACM0 -b arduino:avr:uno | jq -R 'try fromjson catch .'

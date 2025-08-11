#!/bin/sh
set -xe
arduino-cli compile --fqbn arduino:avr:uno ./ultrasonic_sensor.ino
arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno ultrasonic_sensor.ino

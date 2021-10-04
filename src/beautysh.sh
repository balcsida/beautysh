#!/bin/bash
for file in $@
do
  beautysh.py --argument-order --check --english --force-function-style fnonly --function-order --indent-size 2 --line-end --tab "/mnt/${file}"
done
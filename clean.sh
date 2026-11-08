#! /bin/bash

d=`dirname "$0"`
cd "$d"
rm -rf build/ py2apk.egg-info/ || :
find . -type d -name __pycache__ -print0 | xargs -r0 -- rm -rf

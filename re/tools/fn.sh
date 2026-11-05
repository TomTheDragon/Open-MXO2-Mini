#!/bin/sh
# Print decompiled function(s): fn.sh <file.c> <hexaddr>...
f=$1; shift
for a in "$@"; do
	a=$(printf '%08x' "0x$a")
	awk -v F="@ $a" 'index($0,F){p=1;print;next} /^\/\/ ==== /{if(p)exit} p' "$f"
done

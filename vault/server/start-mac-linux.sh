#!/bin/sh
cd "$(dirname "$0")"
export VAULT_SUPERVISED=1
# exit code 3 = update ho gaya, dobara chalao
while true; do node server.js; [ $? -eq 3 ] || break; done

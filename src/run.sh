#!/bin/bash
# Alfred runs with a minimal PATH; pick the best available python3.
for py in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$py" ] && exec "$py" "$(dirname "$0")/brew.py" "$@"
done
echo '{"items":[{"title":"python3 not found","subtitle":"Install Xcode Command Line Tools: xcode-select --install","valid":false}]}'

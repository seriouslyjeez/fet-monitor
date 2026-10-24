#!/bin/sh
# Type-checks the ESP8266 sketch and platform layer against stand-in headers (see stubs/). Catches typos, wrong
# types and missing includes; it can NOT prove the real ESP8266 core API matches, which only a real build does.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SK="$HERE/../fet_bridge"
for f in "$SK"/src/platform/platform.cpp "$SK"/src/platform/portal.cpp; do
  g++ -std=gnu++17 -fsyntax-only -Wall -Wextra -I"$HERE/stubs" "$f"
done
g++ -std=gnu++17 -fsyntax-only -Wall -Wextra -I"$HERE/stubs" -x c++ "$SK/fet_bridge.ino"
echo "syntax check passed"

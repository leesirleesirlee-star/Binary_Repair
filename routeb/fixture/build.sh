#!/usr/bin/env bash
# Build the fixture as a non-PIE ELF (Linux) or Mach-O (macOS, behaviour tests only).
set -euo pipefail

cd "$(dirname "$0")"
CC=${CC:-gcc}

case "$(uname -s)" in
  Linux)
    CFLAGS=(-O0 -g0 -fno-stack-protector -fno-builtin -fno-pie -no-pie)
    ;;
  Darwin)
    # macOS is only used for quick behaviour checks; the real target is ELF.
    CFLAGS=(-O0 -g0 -fno-stack-protector -fno-builtin -fno-pie -Wl,-no_pie)
    ;;
  *)
    echo "unsupported host: $(uname -s)" >&2
    exit 1
    ;;
esac

"$CC" "${CFLAGS[@]}" -o vuln vuln.c
file vuln
echo "built: $(pwd)/vuln"

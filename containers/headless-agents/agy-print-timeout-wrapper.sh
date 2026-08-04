#!/bin/sh
set -eu

needs_print_timeout=0
has_print_timeout=0
for arg in "$@"; do
    case "$arg" in
        -p|--print|--prompt)
            needs_print_timeout=1
            ;;
        --print-timeout|--print-timeout=*)
            has_print_timeout=1
            ;;
    esac
done

if [ "$needs_print_timeout" -eq 1 ] && [ "$has_print_timeout" -eq 0 ]; then
    exec /usr/local/bin/agy-real --print-timeout 900s "$@"
fi

exec /usr/local/bin/agy-real "$@"

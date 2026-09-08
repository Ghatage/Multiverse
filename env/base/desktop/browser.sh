#!/bin/bash
# Reuse the managed profile and its debugging endpoint.
mapfile -t FLAGS < <(grep -v '^\s*#' /opt/fork/chromium.flags | grep -v '^\s*$')
exec chromium "${FLAGS[@]}" about:blank

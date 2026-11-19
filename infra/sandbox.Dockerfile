# Sandbox execution image for CI (E-01).
#
# The sandbox runner (puspa/sandbox/runner.py) executes code as
# `/usr/bin/python3.12` inside an image named `puspa-sandbox:3.12-local`.
# That image is built locally on the dev VM with `docker import` and is
# not published to any registry, so a fresh host has no image to run:
# `Sandbox.check_available()` would stay False and the live container
# tests would skip there too. The CI sandbox lane builds this image
# first, from the public `python:3.12-slim` base, so the four live
# container tests and the Docker computer-backend conformance tests
# execute for real on every PR.
#
# The image carries nothing but a stock CPython: no Puspa code, no
# credentials, no network at run time (`--network none` in the runner).

FROM python:3.12-slim

# The runner invokes /usr/bin/python3.12; the slim image ships
# CPython at /usr/local/bin/python3.12.
RUN ln -s /usr/local/bin/python3.12 /usr/bin/python3.12

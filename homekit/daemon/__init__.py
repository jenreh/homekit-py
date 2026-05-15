"""Daemon mode for homekit-py.

A long-lived process owns one ``HomeKitClient`` and serves CLI invocations over a
Unix domain socket, so the expensive BLE pair-verify handshake is paid once at
boot and amortised across every subsequent command.
"""

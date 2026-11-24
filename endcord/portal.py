# endcord - Copyright (C) 2025-2026 SparkLost. All Rights Reserved.
# Source-available under the Endcord License. See LICENSE for terms.
# Redistribution of modified versions is not permitted.

import logging
import random
import time
from collections import deque

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
logger = logging.getLogger(__name__)


class Portal:
    """xdg-desktop-portal client usng GTK app dbus or jeepney"""

    def __init__(self, dbus=None):
        self.gtk = bool(dbus)
        self.responses = {}
        self.callbacks = {}
        self.subscriptions = []
        if self.gtk:
            from gi.repository import Gio, GLib
            self.gio = Gio
            self.glib = GLib
            self.connection = dbus
            self.context = GLib.MainContext()
            self.source = GLib.timeout_source_new(100)
            self.source.set_callback(lambda *args: True)   # noqa
            self.source.attach(self.context)
        else:
            try:
                from jeepney.io.blocking import open_dbus_connection
            except ImportError:
                raise RuntimeError("no D-Bus backend, run endcord in GUI mode or install jeepney") from None
            self.connection = open_dbus_connection(bus="SESSION", enable_fds=True)
            self.queue = deque()
        self.subscribe(REQUEST_IFACE, "Response", self.on_response)


    def on_response(self, path, member, args):   # noqa
        self.responses[path] = args


    def decode_gvariant(self, value):
        """Convert (gvariant to plain value"""
        if isinstance(value, dict):
            return {key: self.glib.Variant(*val) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.decode_gvariant(item) for item in value]
        return value


    def decode_jeepney(self, value):
        """Decode jeepney message to plain value"""
        if isinstance(value, dict):
            return {key: self.decode_jeepney(val[1]) for key, val in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.decode_jeepney(item) for item in value]
        return value


    def subscribe(self, interface, member, callback):
        """Call callback(path, member, args) for received signal, while iterate is running"""
        if self.gtk:
            def handler(connection, sender, path, iface, signal, params):   # noqa
                callback(path, signal, params.unpack())
            self.context.push_thread_default()   # signals are dispatched in this context
            try:
                self.subscriptions.append(self.connection.signal_subscribe(
                    PORTAL_BUS, interface, member, None, None,
                    self.gio.DBusSignalFlags.NONE, handler,
                ))
            finally:
                self.context.pop_thread_default()

        else:
            from jeepney import MatchRule, message_bus
            from jeepney.io.blocking import Proxy
            rule = MatchRule(type="signal", interface=interface, member=member)
            Proxy(message_bus, self.connection).AddMatch(rule)
            self.subscriptions.append(self.connection.filter(rule, queue=self.queue))
            self.callbacks[(interface, member)] = callback


    def iterate(self):
        """Wait max 0.1s for signals and dispatch them to callbacks"""
        if self.gtk:
            self.context.iteration(True)
            return

        from jeepney import HeaderFields
        try:
            msg = self.connection.recv_until_filtered(self.queue, timeout=0.1)
        except TimeoutError:
            return
        fields = msg.header.fields
        callback = self.callbacks.get((fields[HeaderFields.interface], fields[HeaderFields.member]))
        if callback:
            callback(fields[HeaderFields.path], fields[HeaderFields.member], self.decode_jeepney(msg.body))


    def call(self, path, interface, method, signature, args):
        """Call dbus method of the portal and return reply body as tuple, signature describes args"""
        if self.gtk:
            body = self.glib.Variant(f"({signature})", self.decode_gvariant(args))
            try:
                reply = self.connection.call_sync(PORTAL_BUS, path, interface, method, body, None, self.gio.DBusCallFlags.NONE, 10000, None)
            except self.glib.Error as e:
                raise RuntimeError(f"{method} failed: {e}") from e
            return reply.unpack()

        from jeepney import DBusAddress, DBusErrorResponse, new_method_call
        from jeepney.wrappers import unwrap_msg
        address = DBusAddress(path, bus_name=PORTAL_BUS, interface=interface)
        try:
            return self.decode_jeepney(unwrap_msg(self.connection.send_and_get_reply(new_method_call(address, method, signature, args), timeout=10)))
        except (TimeoutError, DBusErrorResponse) as e:
            raise RuntimeError(f"{method} failed: {e}") from e


    def request(self, interface, method, signature, args, options, timeout=10):
        """Call portal method that responds with signal"""
        options = {"handle_token": ("s", f"endcord{random.randint(0, 0xFFFFFFFF)}")} | options
        handle = self.call(PORTAL_PATH, interface, method, signature + "a{sv}", (*args, options))[0]
        end = time.monotonic() + timeout
        while handle not in self.responses:
            if time.monotonic() > end:
                raise RuntimeError(f"{method}: no response after {timeout}s")
            self.iterate()
        code, results = self.responses.pop(handle)
        if code:
            raise RuntimeError(f"{method} refused or cancelled (code {code})")
        return results


    def open_pipewire(self, session):
        """Get file descriptor of PipeWire remote for screencast session"""
        if self.gtk:
            body = self.glib.Variant("(oa{sv})", (session, {}))
            try:
                reply, fds = self.connection.call_with_unix_fd_list_sync(
                    PORTAL_BUS, PORTAL_PATH, SCREENCAST_IFACE,
                    "OpenPipeWireRemote", body, None,
                    self.gio.DBusCallFlags.NONE, 10000, None, None,
                )
                return fds.get(reply.unpack()[0])
            except self.glib.Error as e:
                raise RuntimeError(f"OpenPipeWireRemote failed: {e}") from e

        else:
            return self.call(PORTAL_PATH, SCREENCAST_IFACE, "OpenPipeWireRemote", "oa{sv}", (session, {}))[0].to_raw_fd()


    def close(self, session=None):
        """Close portal session and release dbus"""
        if session:
            try:
                self.call(session, SESSION_IFACE, "Close", "", ())
            except RuntimeError as e:
                logger.warning(e)

        if self.gtk:
            for subscription in self.subscriptions:
                self.connection.signal_unsubscribe(subscription)
            self.source.destroy()
        else:
            self.connection.close()   # ends all open sessions

import os
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from typix_launcher.fullscreen import (
    ACTIVATED, FULLSCREEN, MANAGER, ForeignToplevelClient, FullscreenSession,
    Toplevel, _string, application_ids,
    activate_existing,
)


def frame(identifier, opcode, payload=b""):
    return struct.pack("=II", identifier, ((len(payload) + 8) << 16) | opcode) + payload


class FullscreenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "com.example.Editor.desktop"
        self.path.write_text("[Desktop Entry]\nName=Editor\nExec=/opt/bin/editor\n")

    def session(self):
        result = FullscreenSession(self.path)
        result.client = Mock()
        result.client.windows = {}
        result._deadline = float("inf")
        return result

    def test_exact_identifiers_without_title_or_substring_matching(self):
        session = self.session()
        for app_id in ["editor-extra", "com.unrelated.editor", ""]:
            session._consider(Toplevel(10, app_id, title="Editor"))
        session.client.set_fullscreen.assert_not_called()
        window = Toplevel(11, "EDITOR")
        session._consider(window)
        session.client.set_fullscreen.assert_called_once_with(window)

    def test_explicit_wrapper_ids_override_inferred_names(self):
        self.path.write_text("[Desktop Entry]\nExec=/bin/sh run.sh\n"
                             "X-TypixDeck-FullscreenAppId=org.example.Player;MyPlayer;\n")
        self.assertEqual(application_ids(self.path), {"org.example.player", "myplayer"})

    def test_startup_class_is_respected_without_matching_generic_interpreter(self):
        self.path.write_text("[Desktop Entry]\nExec=python3 /tmp/viewer.py\nStartupWMClass=Viewer\n")
        self.assertEqual(application_ids(self.path), {"com.example.editor", "viewer"})

    def test_parented_dialogs_and_unrelated_windows_are_never_fullscreened(self):
        session = self.session()
        session._consider(Toplevel(10, "editor", parent=5))
        session._consider(Toplevel(11, "some-other-app"))
        session.client.set_fullscreen.assert_not_called()
        self.assertFalse(session._targets)

    def test_fullscreen_is_one_shot_and_never_toggles_existing_fullscreen(self):
        session = self.session()
        window = Toplevel(10, "editor", states={FULLSCREEN})
        session._consider(window)
        window.states.clear()  # A later user action must remain possible.
        session._consider(window)
        session.client.set_fullscreen.assert_not_called()
        self.assertEqual(session._targets, {10})

    def test_new_window_is_fullscreened_only_once(self):
        session = self.session()
        window = Toplevel(10, "editor")
        session._consider(window)
        session._consider(window)
        session.client.set_fullscreen.assert_called_once_with(window)

    def test_unconfirmed_request_retries_until_confirmation_then_respects_user(self):
        session = self.session()
        session._deadline = 10
        window = Toplevel(10, "editor")
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=1):
            session._consider(window)
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=1.49):
            session._consider(window)
        self.assertEqual(session.client.set_fullscreen.call_count, 1)
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=1.5):
            session._consider(window)
        self.assertEqual(session.client.set_fullscreen.call_count, 2)
        window.states.add(FULLSCREEN)
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=1.6):
            session._consider(window)
        window.states.clear()  # User exits fullscreen after the confirmation.
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=2.5):
            session._consider(window)
        self.assertEqual(session.client.set_fullscreen.call_count, 2)

    def test_unconfirmed_retry_stops_at_startup_deadline(self):
        session = self.session()
        session._deadline = 1
        window = Toplevel(10, "editor")
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=0):
            session._consider(window)
        with patch("typix_launcher.fullscreen.time.monotonic", return_value=2):
            session._consider(window)
        session.client.set_fullscreen.assert_called_once_with(window)

    def test_old_inactive_window_is_untouched_but_reactivation_is_supported(self):
        session = self.session()
        session._baseline.add(10)
        window = Toplevel(10, "editor")
        session._consider(window)
        session.client.set_fullscreen.assert_not_called()
        window.states.add(ACTIVATED)
        session._consider(window)
        session.client.set_fullscreen.assert_called_once_with(window)

    def test_windows_after_startup_deadline_are_not_changed(self):
        session = self.session()
        session._deadline = 0
        session._consider(Toplevel(10, "editor"))
        session.client.set_fullscreen.assert_not_called()

    def test_non_wayland_session_falls_back_without_opening_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "typix_launcher.fullscreen.ForeignToplevelClient"
        ) as factory:
            with FullscreenSession(self.path) as session:
                child = Mock()
                child.wait.return_value = 4
                self.assertEqual(session.wait(child), 4)
            factory.assert_not_called()

    def test_failed_application_returns_without_startup_delay(self):
        session = self.session()
        child = Mock()
        child.poll.return_value = 9
        self.assertEqual(session.wait(child), 9)
        session.client.poll.assert_not_called()

    def test_daemonized_application_keeps_launcher_away_until_window_closes(self):
        session = self.session()
        child = Mock()
        child.poll.return_value = 0
        polls = []
        clock = [0.0]

        def poll(_timeout):
            polls.append(True)
            if len(polls) == 1:
                window = Toplevel(10, "editor")
                session.client.windows[10] = window
                session._consider(window)
            else:
                session.client.windows.clear()
                clock[0] = 11.0  # No grace for normal closure after startup.

        session.client.poll.side_effect = poll
        with patch("typix_launcher.fullscreen.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(session.wait(child), 0)
        self.assertEqual(len(polls), 2)
        child.wait.assert_not_called()

    def test_splash_to_main_gap_keeps_launcher_away_and_fullscreens_replacement(self):
        session = self.session()
        child = Mock()
        child.poll.return_value = 0
        ticks = [0]
        splash = Toplevel(10, "editor")
        main = Toplevel(10, "editor")  # Compositor may reuse the destroyed ID.

        def poll(_timeout):
            ticks[0] += 1
            if ticks[0] == 1:
                session.client.windows[10] = splash
                session._consider(splash)
            elif ticks[0] == 2:
                session.client.windows.clear()
                session._closed(splash)
            elif ticks[0] == 6:
                session.client.windows[10] = main
                session._consider(main)
            elif ticks[0] == 9:
                session.client.windows.clear()
                session._closed(main)

        session.client.poll.side_effect = poll
        with patch("typix_launcher.fullscreen.time.monotonic", side_effect=lambda: ticks[0] / 10):
            self.assertEqual(session.wait(child), 0)
        self.assertEqual(ticks[0], 17)  # Last close at .9 s, grace ends at 1.7 s.
        self.assertEqual(session.client.set_fullscreen.call_count, 2)
        child.wait.assert_not_called()

    def test_empty_window_grace_never_extends_startup_deadline(self):
        session = self.session()
        session.startup_timeout = 0.5
        child = Mock()
        child.poll.return_value = 0
        ticks = [0]
        window = Toplevel(10, "editor")

        def poll(_timeout):
            ticks[0] += 1
            if ticks[0] == 1:
                session.client.windows[10] = window
                session._consider(window)
            elif ticks[0] == 2:
                session.client.windows.clear()
                session._closed(window)

        session.client.poll.side_effect = poll
        with patch("typix_launcher.fullscreen.time.monotonic", side_effect=lambda: ticks[0] / 10):
            self.assertEqual(session.wait(child), 0)
        self.assertEqual(ticks[0], 5)

    def test_missing_window_has_bounded_startup_wait(self):
        session = self.session()
        session.startup_timeout = 0
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        self.assertEqual(session.wait(child), 0)
        session.client.close.assert_called_once()
        child.wait.assert_called_once()

    def test_compositor_disconnect_does_not_break_application_lifecycle(self):
        session = self.session()
        session.client.poll.side_effect = OSError("connection closed")
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 3
        with patch("typix_launcher.fullscreen.FullscreenSession._diagnostic"):
            self.assertEqual(session.wait(child), 3)
        session.client.close.assert_called_once()


class WireProtocolTests(unittest.TestCase):
    def setUp(self):
        connection, self.server = socket.socketpair()
        self.client = ForeignToplevelClient(connection)
        self.addCleanup(self.client.close)
        self.addCleanup(self.server.close)

    def test_protocol_handshake_snapshot_and_idempotent_fullscreen_request(self):
        errors = []
        requests = []
        self.server.settimeout(2)

        def receive():
            header = self.server.recv(8, socket.MSG_WAITALL)
            identifier, info = struct.unpack("=II", header)
            payload = self.server.recv((info >> 16) - 8, socket.MSG_WAITALL) if info >> 16 > 8 else b""
            return identifier, info & 0xFFFF, payload

        def compositor():
            try:
                identifier, opcode, payload = receive()
                self.assertEqual((identifier, opcode), (1, 1))
                registry = struct.unpack("=I", payload)[0]
                self.server.sendall(frame(registry, 0, struct.pack("=I", 77)
                                          + _string(MANAGER) + struct.pack("=I", 3)))
                _, _, payload = receive()  # First sync is queued before bind.
                callback = struct.unpack("=I", payload)[0]
                self.server.sendall(frame(callback, 0, struct.pack("=I", 0)))
                identifier, opcode, payload = receive()
                self.assertEqual((identifier, opcode), (registry, 0))
                version, manager = struct.unpack("=II", payload[-8:])
                self.assertEqual(version, 3)
                handle = 0xFF000000
                data = (frame(manager, 0, struct.pack("=I", handle))
                        + frame(handle, 1, _string("org.example.Player"))
                        + frame(handle, 0, _string("Video"))
                        + frame(handle, 4, struct.pack("=II", 4, FULLSCREEN))
                        + frame(handle, 7, struct.pack("=I", 0))
                        + frame(handle, 5))
                self.server.sendall(data[:5])  # Exercise fragmented framing.
                self.server.sendall(data[5:])
                _, _, payload = receive()
                callback = struct.unpack("=I", payload)[0]
                self.server.sendall(frame(callback, 0, struct.pack("=I", 0)))
                requests.append(receive())
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=compositor, daemon=True)
        worker.start()
        self.client.connect()
        self.assertEqual(self.client.snapshot(), [{
            "app_id": "org.example.Player", "title": "Video", "parent": 0, "fullscreen": True,
        }])
        self.client.set_fullscreen(self.client.windows[0xFF000000])
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(requests, [(0xFF000000, 8, struct.pack("=I", 0))])

    def test_parent_and_state_are_considered_only_when_atomic_done_arrives(self):
        self.client.manager = 4
        self.client.on_done = Mock()
        self.server.sendall(frame(4, 0, struct.pack("=I", 10))
                            + frame(10, 1, _string("editor"))
                            + frame(10, 7, struct.pack("=I", 9))
                            + frame(10, 4, struct.pack("=I", 0)))
        self.client.poll()
        self.client.on_done.assert_not_called()
        self.server.sendall(frame(10, 5))
        self.client.poll()
        self.client.on_done.assert_called_once_with(self.client.windows[10])
        self.assertEqual(self.client.windows[10].parent, 9)

    def test_closed_window_is_removed_and_handle_destroyed(self):
        self.client.windows[10] = Toplevel(10)
        self.server.sendall(frame(10, 6))
        self.client.poll()
        self.assertFalse(self.client.windows)
        self.assertEqual(self.server.recv(8), frame(10, 7))

    def test_malformed_event_fails_cleanly(self):
        self.server.sendall(struct.pack("=II", 1, 7 << 16))
        with self.assertRaises(ValueError):
            self.client.poll()

    def test_activate_unminimizes_and_uses_bound_seat(self):
        window = Toplevel(10)
        with self.assertRaises(OSError):
            self.client.activate(window)
        self.client.seat = 7
        self.client.activate(window)
        self.assertEqual(self.server.recv(20), frame(10, 3) + frame(10, 4, struct.pack("=I", 7)))


class ActivationTests(unittest.TestCase):
    def test_multiple_windows_with_same_app_id_are_ambiguous(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-test"}), \
             patch("typix_launcher.fullscreen.application_ids", return_value={"editor"}), \
             patch("typix_launcher.fullscreen.ForeignToplevelClient") as factory:
            client = factory.return_value
            client.windows = {10: Toplevel(10, "editor", ready=True),
                              11: Toplevel(11, "editor", ready=True)}
            self.assertEqual(activate_existing(Path("editor.desktop")), "unavailable")
            client.activate.assert_not_called()
            client.close.assert_called_once()

    def test_existing_window_focus_refusal_is_not_treated_as_absent(self):
        window = Toplevel(10, "editor", ready=True)
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-test"}), \
             patch("typix_launcher.fullscreen.application_ids", return_value={"editor"}), \
             patch("typix_launcher.fullscreen.ForeignToplevelClient") as factory:
            client = factory.return_value
            client.windows = {10: window}
            self.assertEqual(activate_existing(Path("editor.desktop")), "unavailable")
            client.activate.assert_called_once_with(window)
            client.close.assert_called_once()
            window.states.add(ACTIVATED)
            self.assertEqual(activate_existing(Path("editor.desktop")), "activated")

    def test_dialogs_and_unrelated_windows_are_not_activated(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-test"}), \
             patch("typix_launcher.fullscreen.application_ids", return_value={"editor"}), \
             patch("typix_launcher.fullscreen.ForeignToplevelClient") as factory:
            client = factory.return_value
            client.windows = {10: Toplevel(10, "editor-other", ready=True),
                              11: Toplevel(11, "editor", parent=10, ready=True)}
            self.assertEqual(activate_existing(Path("editor.desktop")), "none")
            client.activate.assert_not_called()


if __name__ == "__main__":
    unittest.main()

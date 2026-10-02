"""Real .desktop fixtures for duplicate aliases and distinct launch contexts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from typix_launcher import desktop


class DuplicateShortcuts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.visible = self.root / 'Desktop'
        self.visible.mkdir()
        directories = patch.object(desktop, 'default_desktop_dirs', return_value=(self.visible,))
        directories.start()
        self.addCleanup(directories.stop)

    def entry(self, filename, command='/bin/echo', extra='', name='Example', directory=None):
        path = (directory or self.visible) / filename
        path.write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec={command}\n{extra}')
        return path

    def test_renamed_copies_same_argv_have_one_tile_and_files_are_preserved(self):
        self.entry('first.desktop', '/bin/echo "hello world"', name='Typix Reader')
        self.entry('second.desktop', "/bin/echo 'hello world'", name='Reader')
        self.assertEqual(len(desktop.load_apps()), 1)
        self.assertEqual(len(list(self.visible.glob('*.desktop'))), 2)

    def test_symlink_and_file_link_and_copy_have_one_tile(self):
        installed = self.root / 'applications'
        installed.mkdir()
        original = self.entry('reader.desktop', directory=installed)
        (self.visible / 'symlink.desktop').symlink_to(original)
        (self.visible / 'link.desktop').write_text(f'[Desktop Entry]\nType=Link\nURL={original.as_uri()}\n')
        self.entry('copy.desktop')
        self.assertEqual(len(desktop.load_apps()), 1)

    def test_same_name_different_documents_remain_distinct(self):
        self.entry('first.desktop', '/bin/echo /tmp/project-a.txt')
        self.entry('second.desktop', '/bin/echo /tmp/project-b.txt')
        self.assertEqual(len(desktop.load_apps()), 2)

    def test_urls_environment_and_terminal_context_are_distinct(self):
        commands = ['browser https://example.org/a', 'browser https://example.org/b',
                    'env APP_MODE=a browser', 'env APP_MODE=b browser']
        for index, command in enumerate(commands):
            self.entry(f'{index}.desktop', command)
        self.entry('terminal.desktop', commands[0], 'Terminal=true\n')
        self.assertEqual(len(desktop.load_apps()), 5)

    def test_working_directories_are_distinct(self):
        self.entry('first.desktop', extra='Path=/tmp/project-a\n')
        self.entry('second.desktop', extra='Path=/tmp/project-b\n')
        self.assertEqual(len(desktop.load_apps()), 2)

    def test_path_and_name_field_codes_preserve_arguments(self):
        self.entry('first.desktop', '/bin/echo %k')
        self.entry('second.desktop', '/bin/echo %k')
        self.entry('third.desktop', '/bin/echo %c', name='Project A')
        self.entry('fourth.desktop', '/bin/echo %c', name='Project B')
        self.assertEqual(len(desktop.load_apps()), 4)

    def test_hidden_alias_does_not_hide_visible_alias(self):
        self.entry('0-hidden.desktop', extra='Hidden=true\n')
        visible = self.entry('1-visible.desktop')
        self.assertEqual([app.path for app in desktop.load_apps()], [visible])

    def test_executable_alias_resolves_but_arguments_remain(self):
        executable = self.root / 'program'
        executable.write_text('#!/bin/sh\n')
        executable.chmod(0o755)
        self.entry('first.desktop', 'program --mode=a')
        self.entry('second.desktop', f'{executable} --mode=a')
        self.entry('third.desktop', f'{executable} --mode=b')
        with patch.object(desktop.shutil, 'which', return_value=str(executable)):
            self.assertEqual(len(desktop.load_apps()), 2)

    def test_dbus_and_fullscreen_id_contexts_are_preserved(self):
        self.entry('first.desktop', extra='DBusActivatable=true\n')
        self.entry('second.desktop', extra='DBusActivatable=true\n')
        self.entry('third.desktop', extra='X-TypixDeck-FullscreenAppId=one;\n')
        self.entry('fourth.desktop', extra='X-TypixDeck-FullscreenAppId=two;\n')
        self.assertEqual(len(desktop.load_apps()), 4)

    def test_symlinked_executable_invocation_paths_remain_distinct(self):
        binary = self.root / 'shared-interpreter'
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        for name in ('venv-a', 'venv-b'):
            directory = self.root / name / 'bin'
            directory.mkdir(parents=True)
            invocation = directory / 'python3'
            invocation.symlink_to(binary)
            self.entry(f'{name}.desktop', f'{invocation} -m application')
        self.assertEqual(len(desktop.load_apps()), 2)

    def test_malformed_command_does_not_break_discovery(self):
        self.entry('bad.desktop', '/bin/echo "unterminated')
        visible = self.entry('good.desktop')
        self.assertEqual([app.path for app in desktop.load_apps()], [visible])


if __name__ == '__main__':
    unittest.main()

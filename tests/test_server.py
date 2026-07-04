from __future__ import annotations

import os
import stat
import tempfile

from momiji.server import Listener


def test_uds_listener_socket_is_world_writable():
    """The socket file must be connectable by other users (e.g. a reverse
    proxy running as a different user), regardless of the process umask."""

    # AF_UNIX paths have a short length limit, so avoid pytest's (long) tmp_path.
    with tempfile.TemporaryDirectory() as tmp_dir:
        socket_path = os.path.join(tmp_dir, "s")
        old_umask = os.umask(0o022)

        try:
            sock = Listener(path=socket_path).bind()
        finally:
            os.umask(old_umask)

        try:
            mode = stat.S_IMODE(os.stat(socket_path).st_mode)
            assert mode & stat.S_IWOTH, f"socket file mode {oct(mode)} is not world-writable"
        finally:
            sock.close()

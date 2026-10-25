"""Worker stdout/stderr must follow worker.log after rotation (#91)."""
from __future__ import annotations

import inspect
import os

from nhj import resources, worker


def test_reopen_stdio_log_follows_a_renamed_inode(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_LOG_MAX_BYTES", 50)
    path = tmp_path / "worker.log"
    path.write_bytes(b"x" * 80)
    saved_out, saved_err = os.dup(1), os.dup(2)
    fd = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        resources.append_log(path, "parent-rotated")
        resources.reopen_stdio_log(path)
        os.write(2, b"fresh\n")
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
    assert b"fresh" in path.read_bytes()
    assert not path.read_bytes().startswith(b"x" * 80)
    backup = tmp_path / "worker.log.1"
    assert backup.is_file()
    assert b"fresh" not in backup.read_bytes()


def test_reopen_stdio_log_rotates_an_oversize_attached_file(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_LOG_MAX_BYTES", 50)
    path = tmp_path / "worker.log"
    path.write_bytes(b"x" * 80)
    saved_out, saved_err = os.dup(1), os.dup(2)
    fd = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        resources.reopen_stdio_log(path)
        os.write(2, b"fresh\n")
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
    assert b"fresh" in path.read_bytes()
    assert (tmp_path / "worker.log.1").read_bytes().startswith(b"x")
    assert path.stat().st_mode & 0o777 == 0o600


def test_reopen_stdio_log_leaves_an_unrelated_regular_file_alone(tmp_path):
    nhj_log = tmp_path / "worker.log"
    nhj_log.write_text("nhj\n")
    operators = tmp_path / "operators-own.log"
    operators.write_text("mine\n")
    saved_out, saved_err = os.dup(1), os.dup(2)
    fd = os.open(operators, os.O_APPEND | os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        before = os.fstat(2)
        resources.reopen_stdio_log(nhj_log)
        after = os.fstat(2)
        os.write(2, b"still-mine\n")
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert operators.read_text().endswith("still-mine\n")
    assert nhj_log.read_text() == "nhj\n"


def test_reopen_stdio_log_leaves_a_pipe_alone(tmp_path):
    path = tmp_path / "worker.log"
    path.write_text("keep\n")
    read_fd, write_fd = os.pipe()
    saved_out, saved_err = os.dup(1), os.dup(2)
    try:
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)
        resources.reopen_stdio_log(path)
        os.write(2, b"still-pipe\n")
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
        os.close(read_fd)
    assert path.read_text() == "keep\n"


def test_worker_loop_reopens_the_queue_log_path():
    assert "reopen_stdio_log(_log_path())" in inspect.getsource(worker.main)

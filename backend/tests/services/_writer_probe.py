"""Observe real artifact inodes at OS boundaries, including dir_fd writers."""

from pathlib import Path
import os
import stat


def inode(value):
    return value.st_dev, value.st_ino


class ArtifactProbe:
    def __init__(self, root, monkeypatch, *, failure=None):
        self.root = Path(root).absolute()
        self.directories = {inode(self.root.stat()): self.root}
        self.created_files = {}
        self.events = []
        self.failure = failure
        self.before_create = None
        self.before_mkdir = None
        self.real_open = os.open
        self.real_mkdir = os.mkdir
        self.real_link = os.link
        self.real_replace = os.replace
        self.real_fsync = os.fsync
        monkeypatch.setattr(os, "open", self.open)
        monkeypatch.setattr(os, "mkdir", self.mkdir)
        monkeypatch.setattr(os, "link", self.link)
        monkeypatch.setattr(os, "replace", self.replace)
        monkeypatch.setattr(os, "fsync", self.fsync)

    def path(self, value, directory_fd=None):
        value = Path(value)
        if value.is_absolute():
            return value
        if directory_fd is not None:
            base = self.directories.get(inode(os.fstat(directory_fd)))
            return base / value if base is not None else None
        return value.absolute()

    def owned(self, path):
        return path is not None and path != self.root and path.is_relative_to(self.root)

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        resolved = self.path(path, dir_fd)
        if self.owned(resolved) and flags & os.O_CREAT and self.before_create:
            self.before_create(resolved, flags)
        descriptor = self.real_open(path, flags, mode, dir_fd=dir_fd)
        metadata = os.fstat(descriptor)
        key = inode(metadata)
        if resolved is not None and (resolved == self.root or self.owned(resolved)):
            if stat.S_ISDIR(metadata.st_mode):
                self.directories[key] = resolved
            elif flags & os.O_CREAT and resolved.name != ".p0-owner":
                self.created_files[key] = resolved
                self.events.append(("create", key, resolved, flags))
            elif key in self.created_files and flags & os.O_ACCMODE == os.O_RDONLY:
                self.events.append(("reopen", key, resolved))
        return descriptor

    def mkdir(self, path, mode=0o777, *, dir_fd=None):
        resolved = self.path(path, dir_fd)
        if self.owned(resolved) and self.before_mkdir:
            self.before_mkdir(resolved, mode)
        self.real_mkdir(path, mode, dir_fd=dir_fd)
        if self.owned(resolved):
            self.directories[inode(os.stat(path, dir_fd=dir_fd, follow_symlinks=False))] = resolved

    def fsync(self, descriptor):
        key = inode(os.fstat(descriptor))
        if key in self.created_files:
            self.events.append(("file_fsync", key, self.created_files[key]))
            if self.failure == "file_fsync":
                raise OSError("synthetic_fsync_failure")
        elif key in self.directories and self.directories[key] != self.root:
            self.events.append(("directory_fsync", key, self.directories[key]))
            if self.failure == "directory_fsync":
                raise OSError("synthetic_directory_fsync_failure")
        return self.real_fsync(descriptor)

    def replace(self, source, destination, *, src_dir_fd=None, dst_dir_fd=None):
        source_path = self.path(source, src_dir_fd)
        destination_path = self.path(destination, dst_dir_fd)
        if self.owned(source_path):
            key = inode(os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False))
            if key in self.created_files:
                self.events.append(("replace_attempt", key, destination_path))
                if self.failure == "replace":
                    raise OSError("synthetic_replace_failure")
                self.real_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
                committed = inode(os.stat(destination, dir_fd=dst_dir_fd, follow_symlinks=False))
                self.events.append(("commit", committed, destination_path))
                return
        self.real_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def link(
        self,
        source,
        destination,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        source_path = self.path(source, src_dir_fd)
        destination_path = self.path(destination, dst_dir_fd)
        if self.owned(source_path):
            key = inode(os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False))
            if key in self.created_files:
                self.events.append(("replace_attempt", key, destination_path))
                if self.failure == "replace":
                    raise OSError("synthetic_replace_failure")
                self.real_link(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                    follow_symlinks=follow_symlinks,
                )
                committed = inode(
                    os.stat(destination, dir_fd=dst_dir_fd, follow_symlinks=False)
                )
                self.events.append(("commit", committed, destination_path))
                return
        self.real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

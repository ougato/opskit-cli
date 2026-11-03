"""Offline hashed-wheel installation into a private native runtime.

No pip, setup.py, entrypoint or package import runs on the host. Wheel selection
is exact and installation only extracts validated ordinary package files.
"""
from __future__ import annotations

from email.parser import BytesParser
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import zipfile

from core.plugin_lock import parse_lock, package_key
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.markers import default_environment

MAX_WHEEL = 128 * 1024**2
MAX_EXPANDED = 512 * 1024**2
MAX_MEMBERS = 16384
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10))}


def install(wheels: list[Path], lock_text: str, destination: Path, *,
            compatible_tags=None, marker_environment=None) -> dict[str, str]:
    """Install one authenticated wheel per locked package to a fresh directory.

    Target tags and marker environment must come from the trusted interpreter
    probe. Defaults refer to the current interpreter, never another architecture.
    """
    _, locked = parse_lock(lock_text)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink() or len(wheels) != len(locked):
        raise ValueError("wheel destination or selection is invalid")
    selected = {}
    requirements = []
    tags = set(sys_tags() if compatible_tags is None else compatible_tags)
    environment = default_environment() if marker_environment is None else dict(marker_environment)
    environment["extra"] = ""
    if not tags or "python_full_version" not in environment:
        raise ValueError("incomplete target interpreter identity")
    files = {}
    expanded = 0
    for path in wheels:
        path = Path(path)
        wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(path.name)
        if not wheel_tags.intersection(tags):
            raise ValueError("wheel is incompatible with target interpreter")
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > MAX_WHEEL or path.suffix != ".whl"
                or any(parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
                       for parent in (path, *path.parents))):
            raise ValueError("unsafe wheel source")
        content = path.read_bytes()
        if len(content) != before.st_size:
            raise ValueError("wheel changed during read")
        digest = hashlib.sha256(content).hexdigest()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise ValueError("wheel entry count exceeded")
            metadata = [member for member in members if member.filename.endswith(".dist-info/METADATA")]
            if len(metadata) != 1 or metadata[0].file_size > 1024**2:
                raise ValueError("wheel metadata is invalid")
            info = BytesParser().parsebytes(archive.read(metadata[0]))
            name, version = package_key(info.get("Name", "")), info.get("Version", "")
            if (name not in locked or locked[name][0] != version or digest not in locked[name][1]
                    or name in selected or name != package_key(wheel_name)
                    or str(wheel_version) != version):
                raise ValueError("wheel does not match dependency lock")
            python_requirement = info.get("Requires-Python")
            if python_requirement and not SpecifierSet(python_requirement).contains(
                    environment["python_full_version"], prereleases=True):
                raise ValueError("wheel Python requirement is not satisfied")
            for raw in info.get_all("Requires-Dist", []):
                requirement = Requirement(raw)
                if requirement.marker is not None and not requirement.marker.evaluate(environment):
                    continue
                if requirement.url or requirement.extras:
                    raise ValueError("URL and extras dependencies are not supported")
                requirements.append(requirement)
            selected[name] = digest
            for member in members:
                portable = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if (portable.is_absolute() or ".." in portable.parts or "\\" in member.orig_filename
                        or "\0" in member.orig_filename
                        or any(any(char in '<>:"|?*' or ord(char) < 32 for char in part)
                               or part.endswith((".", " ")) or part.split(".")[0].casefold() in RESERVED
                               for part in portable.parts)
                        or stat.S_ISLNK(mode) or member.flag_bits & 1):
                    raise ValueError("unsafe wheel member")
                if member.is_dir():
                    continue
                parts = portable.parts
                if not parts:
                    raise ValueError("empty wheel member")
                if parts[0].endswith(".data"):
                    if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                        raise ValueError("unsupported wheel installation scheme")
                    parts = parts[2:]
                # Startup hooks must never execute implicitly in a prepared interpreter.
                if parts[-1].lower().endswith(".pth"):
                    raise ValueError("wheel startup hooks are not supported")
                relative = PurePosixPath(*parts).as_posix()
                folded = relative.casefold()
                if folded in files:
                    raise ValueError("conflicting wheel members")
                expanded += member.file_size
                if expanded > MAX_EXPANDED or len(files) >= MAX_MEMBERS:
                    raise ValueError("wheel expansion budget exceeded")
                files[folded] = (relative, archive.read(member))
    if set(selected) != set(locked):
        raise ValueError("incomplete dependency wheel selection")
    for requirement in requirements:
        dependency = package_key(requirement.name)
        if dependency not in locked or not requirement.specifier.contains(locked[dependency][0], prereleases=True):
            raise ValueError("transitive dependency is absent or incompatible with lock")
    for folded in files:
        for parent in PurePosixPath(folded).parents:
            if parent.as_posix() in files:
                raise ValueError("wheel file conflicts with package directory")
    # No destination mutation until every wheel and path has been validated.
    destination.mkdir()
    for relative, content in files.values():
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(content)
    return selected

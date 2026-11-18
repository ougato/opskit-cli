"""Conservative identity comparison for files opened during update publication."""


def same_file_identity(first, second):
    """Reject missing inode identities; zero is a valid device number on POSIX."""
    first_dev = getattr(first, "st_dev", None)
    second_dev = getattr(second, "st_dev", None)
    first_ino = getattr(first, "st_ino", None)
    second_ino = getattr(second, "st_ino", None)
    if not all(type(value) is int for value in (first_dev, second_dev, first_ino, second_ino)):
        return False
    return (first_dev >= 0 and second_dev >= 0 and first_ino > 0 and second_ino > 0
            and (first_dev, first_ino) == (second_dev, second_ino))

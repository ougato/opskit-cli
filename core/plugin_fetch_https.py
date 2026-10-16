"""Explicit HTTPS guest configuration; no ambient credentials or direct fallback."""
from pathlib import Path

from core.plugin_fetch_source import authentication, remote_source
from core.plugin_fetch_transport import private_write, proxy_binding, quoted


def configure(source, profile, proxy, work):
    """Write only guest-private files and return a credential-free clone URL.

    Caller owns a fresh private guest workspace and total process deadline. The
    network namespace must expose only the designated proxy. This configuration
    does not itself isolate a host process or authorize private network access.
    """
    source = remote_source(source)
    if source.kind != "https":
        raise ValueError("HTTPS source required")
    material = authentication(source, profile).payload()
    address, port, token = proxy_binding(proxy)
    home = Path(work) / "home"
    # All policy validation precedes writes. The caller creates this directory.
    ca = "/etc/ssl/certs/ca-certificates.crt"
    if "ca_pem" in material:
        ca = str(home / "ca.pem")
        private_write(home / "ca.pem", material["ca_pem"])
    config = (
        '[protocol]\n allow = never\n[protocol "https"]\n allow = always\n'
        '[credential]\n helper =\n interactive = false\n'
        '[http]\n sslVerify = true\n followRedirects = false\n'
        ' extraHeader =\n saveCookies = false\n cookieFile = /dev/null\n'
        ' proxyAuthMethod = basic\n'
        ' sslCAInfo = ' + quoted(ca) + '\n'
        ' proxy = ' + quoted(f'http://opskit:{token}@{address}:{port}') + '\n'
    )
    if "authorization" in material:
        # Git URL matching requires a path component boundary, not a string
        # prefix: /repo.git must not authorize /repo.git-other or /other.git.
        config += '[http ' + quoted(source.url) + ']\n extraHeader = ' + quoted('Authorization: ' + material["authorization"]) + '\n'
    private_write(home / "gitconfig", config)
    return source.url

"""Docker 配方业务专属常量"""

DOCKER_GITHUB_API   = "https://api.github.com/repos/moby/moby/releases?per_page=10"

DOCKER_SYSTEM_PACKAGE_VERSION = "system"
DOCKER_VERSIONS_FALLBACK = [DOCKER_SYSTEM_PACKAGE_VERSION]

DOCKER_CE_PACKAGE = "docker-ce"
DOCKER_APT_PACKAGE = "docker.io"
DOCKER_PACKAGES = ["docker.io", "docker-ce", "docker-ce-cli", "docker-ce-rootless-extras", "docker-buildx-plugin", "docker-compose-plugin", "containerd.io", "runc"]
DOCKER_SERVICE   = "docker"
DPKG_QUERY_COMMAND = "dpkg-query"
DPKG_STATUS_INSTALLED = "install ok installed"

"""Docker 配方业务专属常量"""

DOCKER_GITHUB_API   = "https://api.github.com/repos/moby/moby/releases?per_page=10"

DOCKER_VERSIONS_FALLBACK = ["26.1.4", "25.0.5", "24.0.9", "23.0.6"]

DOCKER_PACKAGES = ["docker-ce", "docker-ce-cli", "containerd.io"]
DOCKER_SERVICE   = "docker"

"""Nginx 配方业务专属常量"""

NGINX_GITHUB_API    = "https://api.github.com/repos/nginx/nginx/tags?per_page=10"

NGINX_VERSIONS_FALLBACK = ["1.26.1", "1.24.0", "1.22.1", "1.20.2"]

NGINX_PACKAGE  = "nginx"
NGINX_EXTRA_PACKAGES = ["libnginx-mod-stream"]
NGINX_SERVICE  = "nginx"

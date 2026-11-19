# RustDesk 安装排障

## HTTPS 证书校验失败

RustDesk 的版本查询、安装包下载和公网 IP 探测使用框架的 HTTPX 客户端及下载器
默认使用随依赖提供的 certifi CA，不依赖精简 Linux 系统是否安装系统 CA 包
Nuitka 与 PyInstaller 构建均显式收集 certifi 的 CA 数据文件
企业代理使用私有 CA 时，可通过 SSL_CERT_FILE 指定可信 CA 文件
不会关闭 TLS 校验，不可信证书仍阻止下载安装包

下载通过临时分片写入，完整性检查通过后才生成目标 ZIP，失败的分片不进入安装步骤
出现 CERTIFICATE_VERIFY_FAILED 时，检查当前程序版本、SSL_CERT_FILE / SSL_CERT_DIR 和代理是否替换了服务器证书

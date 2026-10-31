# Upgrade-matrix runner. Network at build time only (PyPI); every cell runs
# with --network none against a local PEP 503 index.
FROM python:3.12-slim
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
RUN pip install -q "pip==25.3" "pipx==1.18.11" "uv==0.13.6" \
 && pip install -q --target /opt/uv08 "uv==0.8.24" \
 && ln -s /opt/uv08/bin/uv /usr/local/bin/uv08 \
 && pip --version && uv --version && uv08 --version && pipx --version
ENV DEEPCTL_TELEMETRY_DISABLED=1 DEEPCTL_TELEMETRY_DSN=http://k@127.0.0.1:9/1 CI=1

# Same base image the commits were labeled in.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ARG SQLGLOT_SHA=eb11aec4be8cb2122afe713a4816662c3f0fb2fa
RUN python -c "import urllib.request as u; u.urlretrieve('https://github.com/tobymao/sqlglot/archive/${SQLGLOT_SHA}.tar.gz', '/tmp/s.tgz')" \
 && mkdir -p /opt/sqlglot && tar xzf /tmp/s.tgz -C /opt/sqlglot --strip-components=1 && rm /tmp/s.tgz \
 && chmod -R a+rX /opt/sqlglot
COPY app /app
RUN chmod -R a+rX /app && python -m compileall -q /app
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "/app/main.py"]

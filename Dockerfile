# HTTrack MCP Server container image.
# Copyright (C) 2026 Tech Ventures VCC
# Licensed under the GNU Affero General Public License v3.0 (see LICENSE).
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends httrack && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastmcp
COPY server.py /app/server.py
WORKDIR /app
CMD ["python", "server.py"]
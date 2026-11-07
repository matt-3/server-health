"""Docker containers and the memory of each one (for "who uses the memory")."""

from __future__ import annotations

import json

from ..i18n import t
from ..metric import Metric
from ..module import Module
from ..parse import parse_size


class Docker(Module):
    name = "docker"
    order = 40

    def detect(self, src, cfg):
        return src.which("docker")

    def discover(self, ctx):
        rc, out, _ = ctx.src.run("docker_ps", ["docker", "ps", "-aq", "--no-trunc"], timeout=20)
        if rc != 0:
            ctx.add(Metric("host.docker", ctx.server_section, "Docker", t("docker.no_access"), "WARN",
                           t("docker.no_access_detail")))
            return
        ids = out.split()
        if ids:
            rc, out, _ = ctx.src.run("docker_inspect", ["docker", "inspect", *ids], timeout=30)
            try:
                ctx.containers = json.loads(out) if rc == 0 else []
            except json.JSONDecodeError:
                ctx.containers = []
        rc, out, _ = ctx.src.run("docker_stats", ["docker", "stats", "--no-stream", "--format",
                                                  "{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}"], timeout=40)
        if rc == 0:
            for line in out.splitlines():
                parts = line.split("|")
                if len(parts) >= 2:
                    ctx.mem_by_container[parts[0]] = parse_size(parts[1].split("/")[0])

    strings = {
        "en": {
            "docker.no_access": "no access",
            "docker.no_access_detail": "docker ps failed: app processes not measured",
        },
        "pt-BR": {
            "docker.no_access": "sem acesso",
            "docker.no_access_detail": "docker ps falhou: processos das apps não medidos",
        },
    }

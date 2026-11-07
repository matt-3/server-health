"""The built-in modules, in the order they run. A module's position decides the order
of its lines in the messages, so new ones go where they read best, not at the end."""

from .backup import Backup
from .clock import Clock
from .docker import Docker
from .dokku import Dokku
from .host import Host
from .http import Http
from .indicators import Indicators
from .maintenance import Maintenance
from .nginx import Nginx
from .ports import Ports
from .postgres import Postgres
from .rails import Rails
from .ssh import Ssh
from .systemd import Systemd
from .tls import Tls
from .updates import Updates

BUILTIN = [Host, Backup, Ssh, Maintenance, Updates, Systemd, Clock, Ports, Docker, Dokku, Http, Nginx, Postgres, Rails,
           Indicators, Tls]

"""Outgoing HTTP, stdlib only. Every notifier posts through here (and the tests replace
`post_json` and `post_multipart` to capture the messages)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid

from . import __version__

LOG = print  # replaced by the CLI with the monitor's logger


def _send(req: urllib.request.Request, timeout: int) -> int:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        LOG(f"network error: {e}")
        return 0


def post_json(url: str, payload: dict, headers: dict | None = None) -> int:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"server-health/{__version__}", **(headers or {})})
    return _send(req, 15)


def post_multipart(url: str, payload: dict, filename: str, content: bytes, content_type: str) -> int:
    """Discord's way to send a file with a message: payload_json plus files[0]."""
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
        "Content-Type: application/json\r\n\r\n".encode(), json.dumps(payload).encode("utf-8"), b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n".encode(), content, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                          "User-Agent": f"server-health/{__version__}"})
    return _send(req, 30)


def get(url: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": f"server-health/{__version__}"})
    return _send(req, 10)

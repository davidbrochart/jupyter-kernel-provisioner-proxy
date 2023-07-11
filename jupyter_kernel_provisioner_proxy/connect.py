import json
import os
import socket
import tempfile
from typing import Dict, Optional, Tuple, Union
from uuid import uuid4

from zmq.asyncio import Socket
import zmq


context = zmq.asyncio.Context()

channel_socket_types = {
    "shell": zmq.ROUTER,
    "iopub": zmq.PUB,
    "stdin": zmq.ROUTER,
    "control": zmq.ROUTER,
}

def create_socket(channel: str, cfg: Dict[str, Union[str, int]], identity: Optional[bytes] = None) -> Socket:
    ip = cfg["ip"]
    port = cfg[f"{channel}_port"]
    url = f"tcp://{ip}:{port}"
    socket_type = channel_socket_types[channel]
    sock = context.socket(socket_type)
    sock.linger = 1000  # set linger to 1s to prevent hangs at exit
    if identity:
        sock.identity = identity
    sock.bind(url)
    return sock

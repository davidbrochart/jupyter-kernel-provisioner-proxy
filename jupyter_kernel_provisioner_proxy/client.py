import asyncio
import hashlib
import hmac
import json
import signal
from contextlib import AsyncExitStack
from inspect import isawaitable
from pathlib import Path
from typing import Optional, Any, List, Dict
from uuid import uuid4

import httpx
from httpx_ws import aconnect_ws
from jupyter_client import KernelProvisionerBase, KernelConnectionInfo
from wsproto.events import TextMessage

from .connect import create_socket

from .message import (
    deserialize_msg_from_ws_v1,
    from_binary,
    get_parent_header,
    get_msg_from_parts,
    get_zmq_parts,
    send_message,
    send_raw_message,
    serialize_msg_to_ws_v1,
    to_binary,
)

class RemoteKernelProvisioner(KernelProvisionerBase):
    """A kernel provisioner that actually talks to a remote kernel
    through the kernel REST API of a Jupyter server.
    """

    kernel_url: str
    kernel_name: str

    def __init__(self, *args, **kwargs):
        self.kernel_url = kwargs.pop("kernel_url")
        self.kernel_name = kwargs.pop("kernel_name")
        super().__init__(*args, **kwargs)
        self.client = httpx.AsyncClient()
        self.cookies = httpx.Cookies()
        self.tasks = []
        i = self.kernel_url.find(":")
        self.ws_url = ("wss" if self.kernel_url[i - 1] == "s" else "ws") + self.kernel_url[i:]
        self.sessions = {}
        self._exit_stack = {}
        self.msg_ids = []
        self.launched = False

    async def pre_launch(self, **kwargs: Any) -> Dict[str, Any]:
        res = await super().pre_launch(**kwargs)
        res["cmd"] = []
        return res

    @property
    def has_process(self) -> bool:
        return True

    async def poll(self) -> Optional[int]:
        r = await self.client.get(
            f"{self.kernel_url}/api/kernels",
            cookies=self.cookies,
        )
        self.cookies.update(r.cookies)
        if self.kernel_id in [d["id"] for d in r.json()]:
            return None

        return 1

    async def wait(self) -> Optional[int]:
        while True:
            if await self.poll() is not None:
                return

    async def send_signal(self, signum: int) -> None:
        if signum == signal.SIGINT:
            r = await self.client.post(
                f"{self.kernel_url}/api/kernels/{self.kernel_id}/interrupt",
                cookies=self.cookies,
            )
            self.cookies.update(r.cookies)

    async def kill(self, restart: bool = False) -> None:
        if restart:
            r = await self.client.post(
                f"{self.kernel_url}/api/kernels/{self.kernel_id}/restart",
                cookies=self.cookies,
            )
            self.cookies.update(r.cookies)
        else:
            r = await self.client.delete(
                f"{self.kernel_url}/api/kernels/{self.kernel_id}",
                cookies=self.cookies,
            )
            self.cookies.update(r.cookies)
            for task in self.tasks:
                task.cancel()

    async def terminate(self, restart: bool = False) -> None:
        if restart:
            r = await self.client.post(
                f"{self.kernel_url}/api/kernels/{self.kernel_id}/restart",
                cookies=self.cookies,
            )
            self.cookies.update(r.cookies)
        else:
            r = await self.client.delete(
                f"{self.kernel_url}/api/kernels/{self.kernel_id}",
                cookies=self.cookies,
            )
            self.cookies.update(r.cookies)
            for task in self.tasks:
                task.cancel()

    async def cleanup(self, restart: bool = False) -> None:
        if restart:
            r = await self.client.post(
                f"{self.kernel_url}/api/kernels/{self.kernel_id}/restart",
                cookies=self.cookies,
            )
            self.cookies.update(r.cookies)

    async def launch_kernel(self, cmd: List[str], **kwargs: Any) -> KernelConnectionInfo:
        km = self.parent
        km.write_connection_file()
        connection_cfg = km.get_connection_info()

        if self.launched:
            return connection_cfg

        parent = Path.cwd()
        child = Path(kwargs["cwd"])
        cwd = child.relative_to(parent)
        data = {
            "kernel": {
                "name": self.kernel_name,
            },
            "name": uuid4().hex,
            "path": f"{cwd}/{uuid4().hex}",
            "type": "notebook",  # FIXME
        }
        r = await self.client.post(
            f"{self.kernel_url}/api/sessions",
            json=data,
            cookies=self.cookies,
        )
        self.cookies.update(r.cookies)
        if r.status_code != 201:
            return {}

        d = r.json()
        session_id = d["id"]
        self.kernel_id = d["kernel"]["id"]
        r = await self.client.get(
            f"{self.kernel_url}/api/sessions",
            cookies=self.cookies,
        )
        self.cookies.update(r.cookies)
        if r.status_code != 200:
            return {}

        d = r.json()
        for session in d:
            if session["id"] == session_id:
                break
        else:
            raise RuntimeError("Could not create a session on the remote kernel server")

        self.key = connection_cfg["key"]

        self.launched = True
        self.shell_channel = create_socket("shell", connection_cfg)
        self.control_channel = create_socket("control", connection_cfg)
        self.stdin_channel = create_socket("stdin", connection_cfg)
        self.iopub_channel = create_socket("iopub", connection_cfg)

        self.name_to_channel = {
            "shell": self.shell_channel,
            "control": self.control_channel,
            "stdin": self.stdin_channel,
            "iopub": self.iopub_channel,
        }

        self.tasks += [
            asyncio.create_task(self.listen_zmq("shell")),
            asyncio.create_task(self.listen_zmq("stdin")),
            asyncio.create_task(self.listen_zmq("control")),
        ]

        self.launched = True
        return connection_cfg

    async def connect_ws(self, session_id):
        async with AsyncExitStack() as exit_stack:
            ws = aconnect_ws(
                f"{self.ws_url}/api/kernels/{self.kernel_id}/channels",
                self.client,
                params={"session_id": session_id},
                cookies=self.cookies,
                subprotocols=["v1.kernel.websocket.jupyter.org"],
            )
            websocket = await exit_stack.enter_async_context(ws)
            self._exit_stack[websocket] = exit_stack.pop_all()

        return websocket

    async def listen_zmq(self, channel_name: str):
        channel = self.name_to_channel[channel_name]

        while True:
            idents, parts = await get_zmq_parts(channel)
            header = json.loads(parts[1])
            session_id = header["session"]

            if session_id in self.sessions:
                session = await self.get_session(session_id)
                channel_ids = session["channel_ids"]
                if channel in channel_ids:
                    channel_ids[channel].update(set(idents))
                else:
                    channel_ids[channel] = set(idents)
            else:
                channel_ids = {channel: set(idents)}
                session = await self.create_session(session_id, channel_ids)
                self.tasks.append(asyncio.create_task(self.listen_web(session["websocket"], session_id, channel_ids)))

            websocket = session["websocket"]
            lock = session["lock"]
            await self.await_websocket(session)
            if websocket.subprotocol == "v1.kernel.websocket.jupyter.org":
                bin_msg = serialize_msg_to_ws_v1(parts, channel_name)
                async with lock:
                    try:
                        await websocket.send_bytes(bin_msg)
                    except Exception:
                        await self._exit_stack[websocket].__aexit__()
                        del self.sessions[session_id]
            else:
                # default, "legacy" protocol
                parent_header = get_parent_header(parts)
                msg = get_msg_from_parts(parts, parent_header=parent_header)
                msg["channel"] = channel_name
                async with lock:
                    try:
                        bmsg = to_binary(msg)
                        if bmsg is None:
                            await websocket.send_json(msg)
                        else:
                            await websocket.send_bytes(bmsg)
                    except Exception as e:
                        print(f"{e=}")
                        await self._exit_stack[websocket].__aexit__()
                        del self.sessions[session_id]


    async def get_session(self, session_id: str):
        session = self.sessions[session_id]
        lock = session["lock"]
        await self.await_websocket(session)
        return session

    async def create_session(self, session_id: str, channel_ids):
        websocket = self.connect_ws(session_id)
        lock = asyncio.Lock()
        self.sessions[session_id] = session = {"websocket": websocket, "lock": lock, "channel_ids": channel_ids}
        await self.await_websocket(session)
        return session

    async def await_websocket(self, session):
        websocket = session["websocket"]
        # check a first time to return early
        if not isawaitable(websocket):
            return

        async with session["lock"]:
            # check a second time because it might have been awaited already
            if isawaitable(websocket):
                websocket = await websocket
                session["websocket"] = websocket

    async def listen_web(self, websocket, session_id, channel_ids):
        if websocket.subprotocol == "v1.kernel.websocket.jupyter.org":
            while True:
                messages = []
                while True:
                    try:
                        msg = await websocket.receive()
                    except Exception:
                        try:
                            await self._exit_stack[websocket].__aexit__()
                        except:
                            pass
                        del self.sessions[session_id]
                        return
                    messages.append(msg.data)
                    if msg.message_finished:
                        break
                message = b"".join(messages)
                channel_name, parts = deserialize_msg_from_ws_v1(message)

                header = json.loads(parts[0])
                msg_id = header["msg_id"]
                if self.skip_message(msg_id):
                    continue

                channel = self.name_to_channel[channel_name]
                idents = None if channel == self.iopub_channel else list(channel_ids[channel])
                await send_raw_message(parts, channel, self.key, idents)
        else:
            while True:
                msg = await websocket.receive()
                if isinstance(msg, TextMessage):
                    message = json.loads(msg.data)
                else:
                    message = from_binary(msg.data)
                msg_id = message["header"]["msg_id"]
                if self.skip_message(msg_id):
                    continue

                channel_name = message.pop("channel")
                channel = self.name_to_channel[channel_name]
                idents = None if channel == self.iopub_channel else list(channel_ids[channel])
                await send_message(message, channel, self.key, idents)

    def skip_message(self, msg_id: str) -> bool:
        if msg_id in self.msg_ids:
            res = True
        else:
            res = False
            self.msg_ids.append(msg_id)
            if len(self.msg_ids) > 10:
                del self.msg_ids[0]
        return res

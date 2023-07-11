import hashlib
import hmac
import json
import struct
from typing import Any, Dict, List, Optional, Tuple, cast

from dateutil.parser import parse as dateutil_parse
from zmq.asyncio import Socket
from zmq.utils import jsonapi


DELIM = b"<IDS|MSG>"


def to_binary(msg: Dict[str, Any]) -> Optional[bytes]:
    if not msg["buffers"]:
        return None
    buffers = msg.pop("buffers")
    bmsg = json.dumps(msg).encode("utf8")
    buffers.insert(0, bmsg)
    n = len(buffers)
    offsets = [4 * (n + 1)]
    for b in buffers[:-1]:
        offsets.append(offsets[-1] + len(b))
    header = struct.pack("!" + "I" * (n + 1), n, *offsets)
    buffers.insert(0, header)
    return b"".join(buffers)


def from_binary(bmsg: bytes) -> Dict[str, Any]:
    n = struct.unpack("!i", bmsg[:4])[0]
    offsets = list(struct.unpack("!" + "I" * n, bmsg[4 : 4 * (n + 1)]))  # noqa
    offsets.append(None)
    buffers = []
    for start, stop in zip(offsets[:-1], offsets[1:]):
        buffers.append(bmsg[start:stop])
    msg = json.loads(buffers[0].decode("utf8"))
    msg["buffers"] = buffers[1:]
    return msg


async def send_raw_message(parts: List[bytes], sock: Socket, key: str, idents: Optional[List[bytes]] = None) -> None:
    msg = parts[:4]
    buffers = parts[4:]
    to_send = []
    if idents is not None:
        to_send += idents
    to_send += [DELIM, sign(msg, key)] + msg + buffers
    await sock.send_multipart(to_send)


def deserialize_msg_from_ws_v1(ws_msg: bytes) -> Tuple[str, List[bytes]]:
    offset_number = int.from_bytes(ws_msg[:8], "little")
    offsets = [
        int.from_bytes(ws_msg[8 * (i + 1) : 8 * (i + 2)], "little")  # noqa
        for i in range(offset_number)
    ]
    channel = ws_msg[offsets[0] : offsets[1]].decode("utf-8")  # noqa
    msg_list = [ws_msg[offsets[i] : offsets[i + 1]] for i in range(1, offset_number - 1)]  # noqa
    return channel, msg_list


async def get_zmq_parts(socket: Socket) -> Tuple[List[bytes], List[bytes]]:
    try:
        parts = await socket.recv_multipart()
    except Exception as e:
        print(f"{e=}")
    idents, parts = feed_identities(parts)
    return idents, parts


def get_msg_from_parts(
    parts: List[bytes], parent_header: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return deserialize(parts, parent_header=parent_header)


def serialize_msg_to_ws_v1(msg_list: List[bytes], channel: str) -> bytes:
    msg_list = msg_list[1:]
    channel_b = channel.encode("utf-8")
    offsets = []
    offsets.append(8 * (1 + 1 + len(msg_list) + 1))
    offsets.append(len(channel_b) + offsets[-1])
    for msg in msg_list:
        offsets.append(len(msg) + offsets[-1])
    offset_number = len(offsets).to_bytes(8, byteorder="little")
    offsets_b = [offset.to_bytes(8, byteorder="little") for offset in offsets]
    bin_msg = b"".join([offset_number] + offsets_b + [channel_b] + msg_list)
    return bin_msg


def get_parent_header(parts: List[bytes]) -> Dict[str, Any]:
    return unpack(parts[2])

def pack(obj: Dict[str, Any]) -> bytes:
    return jsonapi.dumps(obj)

def unpack(s: bytes) -> Dict[str, Any]:
    return cast(Dict[str, Any], jsonapi.loads(s))

def deserialize(
    msg_list: List[bytes],
    parent_header: Optional[Dict[str, Any]] = None,
    change_str_to_date: bool = False,
) -> Dict[str, Any]:
    _str_to_date = str_to_date if change_str_to_date else lambda x: x
    message: Dict[str, Any] = {}
    header = unpack(msg_list[1])
    message["header"] = _str_to_date(header)
    message["msg_id"] = header["msg_id"]
    message["msg_type"] = header["msg_type"]
    if parent_header:
        message["parent_header"] = parent_header
    else:
        message["parent_header"] = _str_to_date(unpack(msg_list[2]))
    message["metadata"] = unpack(msg_list[3])
    message["content"] = unpack(msg_list[4])
    message["buffers"] = [memoryview(b) for b in msg_list[5:]]
    return message


def feed_identities(msg_list: List[bytes]) -> Tuple[List[bytes], List[bytes]]:
    idx = msg_list.index(DELIM)
    return msg_list[:idx], msg_list[idx + 1 :]  # noqa


def str_to_date(obj: Dict[str, Any]) -> Dict[str, Any]:
    if "date" in obj:
        obj["date"] = dateutil_parse(obj["date"])
    return obj


def date_to_str(obj: Dict[str, Any]):
    if "date" in obj and type(obj["date"]) is not str:
        obj["date"] = obj["date"].isoformat().replace("+00:00", "Z")
    return obj


def sign(msg_list: List[bytes], key: bytes) -> bytes:
    auth = hmac.new(key, digestmod=hashlib.sha256)
    h = auth.copy()
    for m in msg_list:
        h.update(m)
    return h.hexdigest().encode()


async def send_message(
    msg: Dict[str, Any], sock: Socket, key: str, idents: Optional[List[bytes]] = None, change_date_to_str: bool = False
) -> None:
    await sock.send_multipart(serialize(msg, key, idents=idents, change_date_to_str=change_date_to_str), copy=True)

def serialize(msg: Dict[str, Any], key: str, idents: Optional[List[bytes]] = None, change_date_to_str: bool = False) -> List[bytes]:
    _date_to_str = date_to_str if change_date_to_str else lambda x: x
    message = [
        pack(_date_to_str(msg["header"])),
        pack(_date_to_str(msg["parent_header"])),
        pack(_date_to_str(msg["metadata"])),
        pack(_date_to_str(msg.get("content", {}))),
    ]
    to_send = []
    if idents is not None:
        to_send += idents
    to_send += [DELIM, sign(message, key)] + message + msg.get("buffers", [])
    return to_send

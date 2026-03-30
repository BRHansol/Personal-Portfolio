from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def ts_ms() -> int:
    return int(time.time() * 1000)


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def jdump(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


async def read_json_lines(reader: asyncio.StreamReader):
    while True:
        line = await reader.readline()
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            yield {"type": "invalid", "raw": line.decode("utf-8", errors="replace")}


@dataclass
class ChannelSim:
    delay_ms: float = 0.0
    delay_jitter_ms: float = 0.0
    loss: float = 0.0
    rng: random.Random = random.Random(7)

    async def maybe_delay(self) -> None:
        if self.delay_ms <= 0 and self.delay_jitter_ms <= 0:
            return
        jitter = self.rng.uniform(-self.delay_jitter_ms, self.delay_jitter_ms) if self.delay_jitter_ms else 0.0
        d = max(0.0, (self.delay_ms + jitter) / 1000.0)
        if d > 0:
            await asyncio.sleep(d)

    def drop(self) -> bool:
        if self.loss <= 0:
            return False
        return self.rng.random() < self.loss


async def write_json(writer: asyncio.StreamWriter, msg: Dict[str, Any]) -> None:
    writer.write(jdump(msg))
    await writer.drain()


@dataclass
class NodeInfo:
    name: str
    writer: asyncio.StreamWriter
    pos: Optional[Tuple[float, float]] = None


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def parse_send_line(line: str, default_to: Optional[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    Client input formats:
      /to <NODE> <message...>
      @<NODE> <message...>
      <message...>   (uses default_to if set)

    Optional multi-hop plan (any node can be middle):
      /to <DST> <message...> /via N36,N34
      @<DST> <message...> /via N36 N34
    """
    s = line.strip()
    if not s:
        return None, None, []

    via_plan: List[str] = []
    if " /via " in s:
        s, via_part = s.split(" /via ", 1)
        via_part = via_part.strip()
        if via_part:
            # allow "N36,N34" or "N36 N34"
            via_part = via_part.replace(",", " ")
            via_plan = [x for x in (p.strip() for p in via_part.split()) if x]

    if s.startswith("/to "):
        rest = s[4:].strip()
        if " " not in rest:
            return None, None, []
        dst, msg = rest.split(" ", 1)
        return dst.strip(), msg.strip(), via_plan
    if s.startswith("@"):
        rest = s[1:].strip()
        if " " not in rest:
            return None, None, []
        dst, msg = rest.split(" ", 1)
        return dst.strip(), msg.strip(), via_plan
    if default_to:
        return default_to, s, via_plan
    return None, None, []


def parse_pos_line(line: str) -> Optional[Tuple[float, float]]:
    s = line.strip()
    if not s.startswith("/pos "):
        return None
    rest = s[5:].strip()
    parts = rest.split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


def compute_auto_path(
    src: str,
    dst: str,
    nodes: Dict[str, NodeInfo],
    max_hop_distance: float,
    max_hops: int,
) -> Optional[List[str]]:
    """
    Auto multi-hop planning based on node positions.
    - Edge exists when both nodes have /pos and distance <= max_hop_distance.
    - Returns shortest-hop path up to max_hops.
    """
    if src not in nodes or dst not in nodes:
        return None
    if src == dst:
        return [src]
    if nodes[src].pos is None or nodes[dst].pos is None:
        return None

    adj: Dict[str, List[str]] = {k: [] for k in nodes.keys()}
    for a, ai in nodes.items():
        if ai.pos is None:
            continue
        for b, bi in nodes.items():
            if a == b or bi.pos is None:
                continue
            if _dist(ai.pos, bi.pos) <= max_hop_distance:
                adj[a].append(b)

    q: List[List[str]] = [[src]]
    seen = {src}
    while q:
        p = q.pop(0)
        if len(p) - 1 > max_hops:
            continue
        last = p[-1]
        if last == dst:
            return p
        for nb in adj.get(last, []):
            if nb in seen:
                continue
            seen.add(nb)
            q.append(p + [nb])
    return None


async def run_router(name: str, host: str, port: int, sim: ChannelSim) -> int:
    eprint(f"[{name}] ROUTER on {host}:{port} (delay={sim.delay_ms}±{sim.delay_jitter_ms}ms, loss={sim.loss})")
    if hasattr(sim, "max_hop_distance") and hasattr(sim, "max_hops"):
        eprint(f"[{name}] auto-route enabled: max_hop_distance={sim.max_hop_distance}, max_hops={sim.max_hops}")
    if hasattr(sim, "distance_delay_ms_per_unit") or hasattr(sim, "loss_at_max"):
        eprint(
            f"[{name}] distance model: delay_per_unit={getattr(sim,'distance_delay_ms_per_unit',0.0)}ms, loss_at_max={getattr(sim,'loss_at_max',0.0)}"
        )

    clients: Dict[str, NodeInfo] = {}

    def link_distance(a: str, b: str) -> Optional[float]:
        ai = clients.get(a)
        bi = clients.get(b)
        if ai is None or bi is None or ai.pos is None or bi.pos is None:
            return None
        return _dist(ai.pos, bi.pos)

    async def simulate_magnetic_link(hop_src: str, hop_dst: str) -> bool:
        """
        Returns True if the frame should be dropped.
        Delay and loss increase with distance (if both nodes have /pos).
        This makes "very far direct" worse than "multi-hop via closer relays".
        """
        d = link_distance(hop_src, hop_dst)
        max_d = float(getattr(sim, "max_hop_distance", 0.0) or 0.0)

        # Base delay/jitter always applies
        await sim.maybe_delay()

        if d is None or max_d <= 0:
            return sim.drop()

        ratio = min(3.0, max(0.0, d / max_d))

        # Add distance penalty delay (non-linear):
        # - We intentionally make long links disproportionately worse so that
        #   multi-hop via closer relays can be faster than a single long hop.
        # - ratio = d / max_d
        # - extra_delay_ms ≈ distance_delay_ms_per_unit * (ratio^2) * max_d
        per_unit = float(getattr(sim, "distance_delay_ms_per_unit", 0.0) or 0.0)
        if per_unit > 0:
            extra_delay_ms = per_unit * (ratio * ratio) * max_d
            await asyncio.sleep(max(0.0, extra_delay_ms / 1000.0))

        # Distance-based loss: baseline + scaled component (squared ratio)
        loss_at_max = float(getattr(sim, "loss_at_max", 0.0) or 0.0)
        p = float(sim.loss) + (ratio * ratio) * loss_at_max
        p = max(0.0, min(0.95, p))
        return sim.rng.random() < p

    async def safe_send(target: str, msg: Dict[str, Any]) -> bool:
        ni = clients.get(target)
        if ni is None:
            return False
        w = ni.writer
        try:
            await write_json(w, msg)
            return True
        except Exception:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
            clients.pop(target, None)
            return False

    async def broadcast(sender: str, msg: Dict[str, Any]) -> None:
        for nid in list(clients.keys()):
            if nid == sender:
                continue
            await safe_send(nid, msg)

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        node_id: Optional[str] = None
        eprint(f"[{name}] inbound connection from {peer}")
        try:
            # Expect first message: {"type":"hello","name":"N12"}
            hello_line = await reader.readline()
            if not hello_line:
                return
            try:
                hello = json.loads(hello_line.decode("utf-8"))
            except json.JSONDecodeError:
                await write_json(writer, {"type": "error", "error": "Expected hello JSON as first line"})
                return
            if hello.get("type") != "hello" or not isinstance(hello.get("name"), str):
                await write_json(writer, {"type": "error", "error": "Invalid hello"})
                return

            node_id = hello["name"].strip()
            if not node_id:
                await write_json(writer, {"type": "error", "error": "Empty node name"})
                return

            # Replace older connection if same name exists
            if node_id in clients:
                try:
                    clients[node_id].writer.close()
                except Exception:
                    pass
            clients[node_id] = NodeInfo(name=node_id, writer=writer, pos=None)
            await write_json(
                writer,
                {
                    "type": "hello_ack",
                    "router": name,
                    "you": node_id,
                    "peers": sorted(clients.keys()),
                    "hint": "Use /pos x y on clients to enable auto multi-hop routing",
                },
            )
            eprint(f"[{name}] registered client {node_id} from {peer}")

            # Notify others (best-effort)
            await broadcast(node_id, {"type": "peer_join", "peer": node_id, "at_ms": ts_ms()})

            async for msg in read_json_lines(reader):
                if msg.get("type") == "pos":
                    x = msg.get("x")
                    y = msg.get("y")
                    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                        if node_id in clients:
                            clients[node_id].pos = (float(x), float(y))
                        await write_json(writer, {"type": "pos_ack", "x": float(x), "y": float(y)})
                    continue

                if msg.get("type") != "data":
                    continue
                src = msg.get("src")
                dst = msg.get("dst")
                payload = msg.get("payload")
                via_plan = msg.get("via_plan") or []
                if src != node_id:
                    await write_json(writer, {"type": "error", "error": "src mismatch", "expected": node_id})
                    continue
                if not isinstance(dst, str) or not isinstance(payload, str):
                    await write_json(writer, {"type": "error", "error": "Invalid data message"})
                    continue
                if not isinstance(via_plan, list) or any(not isinstance(x, str) for x in via_plan):
                    await write_json(writer, {"type": "error", "error": "Invalid via_plan"})
                    continue

                via: List[str] = list(msg.get("via", []))
                via.append(name)
                msg["via"] = via
                msg["routed_at_ms"] = ts_ms()

                # Auto-route: if no explicit via_plan, compute a relay chain using node positions
                if dst != "*" and not via_plan and hasattr(sim, "max_hop_distance") and hasattr(sim, "max_hops"):
                    try:
                        path = compute_auto_path(
                            src=str(src),
                            dst=str(dst),
                            nodes=clients,
                            max_hop_distance=float(sim.max_hop_distance),
                            max_hops=int(sim.max_hops),
                        )
                    except Exception:
                        path = None
                    if path and len(path) >= 2:
                        relays = [n for n in path[1:-1] if n not in (src, dst, name)]
                        if relays:
                            msg["final_dst"] = dst
                            msg["remaining_hops"] = relays[:]  # relays only
                            msg["dst"] = relays[0]

                # Multi-hop: deliver to next hop first, letting clients forward automatically.
                # If via_plan is present, the first hop becomes dst, and final destination is kept in final_dst.
                if dst != "*" and via_plan:
                    # sanitize: remove src/dst/router duplicates
                    cleaned = []
                    for hop in via_plan:
                        hop = hop.strip()
                        if not hop or hop in (src, dst, name):
                            continue
                        if hop not in cleaned:
                            cleaned.append(hop)
                    msg["final_dst"] = dst
                    msg["remaining_hops"] = cleaned  # relays only; final_dst is implied
                    msg["dst"] = cleaned[0] if cleaned else dst

                if dst == "*":
                    await broadcast(src, msg)
                    continue

                # Simulate magnetic channel for this hop (src -> msg["dst"])
                hop_src = str(msg.get("src", ""))
                hop_dst = str(msg.get("dst", ""))
                if await simulate_magnetic_link(hop_src, hop_dst):
                    d = link_distance(hop_src, hop_dst)
                    d_str = f"{d:.1f}" if isinstance(d, (int, float)) else "?"
                    eprint(f"[{name}] DROPPED frame {hop_src} -> {hop_dst} (d={d_str})")
                    continue

                ok = await safe_send(msg["dst"], msg)
                if not ok:
                    await write_json(writer, {"type": "error", "error": "dst not connected", "dst": msg["dst"]})
        except Exception as ex:
            eprint(f"[{name}] router client error: {ex}")
        finally:
            if node_id and clients.get(node_id) and clients[node_id].writer is writer:
                clients.pop(node_id, None)
                await broadcast(node_id, {"type": "peer_leave", "peer": node_id, "at_ms": ts_ms()})
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            eprint(f"[{name}] connection closed: {peer} ({node_id or 'unregistered'})")

    server = await asyncio.start_server(handle_client, host=host, port=port)
    async with server:
        await server.serve_forever()
    return 0


async def run_client(
    name: str,
    host: str,
    port: int,
    default_to: Optional[str],
    initial_pos: Optional[Tuple[float, float]],
) -> int:
    eprint(f"[{name}] CLIENT connect to {host}:{port}")
    reader, writer = await asyncio.open_connection(host=host, port=port)
    await write_json(writer, {"type": "hello", "name": name, "at_ms": ts_ms()})

    # read hello ack (best effort)
    ack = await reader.readline()
    if ack:
        try:
            obj = json.loads(ack.decode("utf-8"))
            eprint(f"[{name}] router={obj.get('router')} peers={obj.get('peers')}")
        except Exception:
            pass

    eprint(f"[{name}] Ready. Use `/to NODE message` (or `/to * broadcast`). Ctrl+C to exit.")
    if default_to:
        eprint(f"[{name}] Default recipient: {default_to} (type message without /to)")
    eprint(f"[{name}] Set position (for auto multi-hop): /pos x y")
    if initial_pos is not None:
        x, y = initial_pos
        await write_json(writer, {"type": "pos", "name": name, "x": x, "y": y, "at_ms": ts_ms()})
        eprint(f"[{name}] initial position sent: ({x}, {y})")

    loop = asyncio.get_running_loop()

    async def stdin_task():
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                return
            pos = parse_pos_line(line)
            if pos is not None:
                x, y = pos
                await write_json(writer, {"type": "pos", "name": name, "x": x, "y": y, "at_ms": ts_ms()})
                continue
            dst, msg_text, via_plan = parse_send_line(line.rstrip("\n"), default_to=default_to)
            if not dst or not msg_text:
                eprint(f"[{name}] format: /to <NODE|*> <message>")
                eprint(f"[{name}] optional: add ' /via N36,N34' to force middle nodes")
                continue
            msg = {
                "type": "data",
                "origin": name,  # original sender (stays constant across relays)
                "src": name,     # current hop sender (must match connected client name)
                "dst": dst,
                "via": [name],
                "sent_at_ms": ts_ms(),
                "payload": msg_text,
                "via_plan": via_plan,
            }
            await write_json(writer, msg)

    async def recv_task():
        async for msg in read_json_lines(reader):
            t = msg.get("type")
            if t == "data":
                # If this client is acting as a relay hop, auto-forward.
                final_dst = msg.get("final_dst")
                remaining = msg.get("remaining_hops") or []
                dst = msg.get("dst", "?")

                if isinstance(final_dst, str) and dst == name and final_dst != name:
                    if not isinstance(remaining, list) or any(not isinstance(x, str) for x in remaining):
                        print("[ERROR] invalid remaining_hops", flush=True)
                        continue

                    next_hop = remaining[1] if remaining and remaining[0] == name and len(remaining) > 1 else None
                    # remaining_hops is relays only; after last relay, go to final_dst
                    if remaining and remaining[0] == name:
                        remaining = remaining[1:]
                    next_dst = remaining[0] if remaining else final_dst

                    via_list: List[str] = list(msg.get("via", []))
                    if not via_list or via_list[-1] != name:
                        via_list.append(name)

                    fwd = dict(msg)
                    # On forward: src must become THIS node (router validates src==connection name)
                    # Keep original sender in origin.
                    if "origin" not in fwd:
                        fwd["origin"] = fwd.get("src", "?")
                    fwd["src"] = name
                    fwd["dst"] = next_dst
                    fwd["via"] = via_list
                    fwd["remaining_hops"] = remaining
                    fwd["forwarded_at_ms"] = ts_ms()
                    await write_json(writer, fwd)
                    print(f"[RELAY] forwarded to {next_dst} (final={final_dst})", flush=True)
                    continue

                via = " -> ".join(msg.get("via", []))
                payload = msg.get("payload", "")
                origin = msg.get("origin") or msg.get("src", "?")
                shown_dst = final_dst if isinstance(final_dst, str) else msg.get("dst", "?")
                sent_at = msg.get("sent_at_ms")
                age = f"{ts_ms() - sent_at}ms" if isinstance(sent_at, int) else "--"
                print(f"[RECV] {origin} -> {shown_dst} | via {via} | age {age} | {payload}", flush=True)
            elif t in ("peer_join", "peer_leave"):
                print(f"[INFO] {t}: {msg.get('peer')}", flush=True)
            elif t == "error":
                print(f"[ERROR] {msg.get('error')} {msg.get('dst') or ''}".rstrip(), flush=True)
            else:
                print(f"[INFO] {msg}", flush=True)

    try:
        await asyncio.gather(stdin_task(), recv_task())
    finally:
        writer.close()
        await writer.wait_closed()
    return 0


async def run_listen(name: str, host: str, port: int) -> int:
    eprint(f"[{name}] LISTEN on {host}:{port}")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        eprint(f"[{name}] connection from {peer}")
        try:
            async for msg in read_json_lines(reader):
                if msg.get("type") == "data":
                    via = " -> ".join(msg.get("via", []))
                    payload = msg.get("payload", "")
                    src = msg.get("src", "?")
                    dst = msg.get("dst", "?")
                    sent_at = msg.get("sent_at_ms")
                    age = f"{ts_ms() - sent_at}ms" if isinstance(sent_at, int) else "--"
                    print(f"[RECV] {src} -> {dst} | via {via} | age {age} | {payload}", flush=True)
                else:
                    print(f"[RECV] {msg}", flush=True)
        except Exception as ex:
            eprint(f"[{name}] client error: {ex}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            eprint(f"[{name}] connection closed: {peer}")

    server = await asyncio.start_server(handle_client, host=host, port=port)
    async with server:
        await server.serve_forever()
    return 0


async def run_connect(name: str, host: str, port: int, dst: str) -> int:
    eprint(f"[{name}] CONNECT to {host}:{port} (dst={dst})")
    reader, writer = await asyncio.open_connection(host=host, port=port)
    eprint(f"[{name}] connected. Type messages and press Enter.")

    loop = asyncio.get_running_loop()

    async def stdin_lines():
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                return
            yield line.rstrip("\n")

    try:
        async for line in stdin_lines():
            if not line:
                continue
            msg = {
                "type": "data",
                "src": name,
                "dst": dst,
                "via": [name],
                "sent_at_ms": ts_ms(),
                "payload": line,
            }
            writer.write(jdump(msg))
            await writer.drain()
            eprint(f"[{name}] sent {len(line)} chars")
    finally:
        writer.close()
        await writer.wait_closed()
    return 0


async def run_relay(
    name: str,
    listen_host: str,
    listen_port: int,
    forward_host: str,
    forward_port: int,
    sim: ChannelSim,
) -> int:
    eprint(f"[{name}] RELAY listen {listen_host}:{listen_port} -> forward {forward_host}:{forward_port}")

    async def handle_inbound(in_reader: asyncio.StreamReader, in_writer: asyncio.StreamWriter):
        peer = in_writer.get_extra_info("peername")
        eprint(f"[{name}] inbound from {peer}")

        out_reader: Optional[asyncio.StreamReader] = None
        out_writer: Optional[asyncio.StreamWriter] = None

        async def ensure_out():
            nonlocal out_reader, out_writer
            if out_writer is not None:
                return
            out_reader, out_writer = await asyncio.open_connection(host=forward_host, port=forward_port)
            eprint(f"[{name}] connected to next hop {forward_host}:{forward_port}")

        try:
            async for msg in read_json_lines(in_reader):
                if msg.get("type") != "data":
                    continue

                if sim.drop():
                    eprint(f"[{name}] DROPPED frame from {msg.get('src')} (loss simulated)")
                    continue

                await sim.maybe_delay()
                await ensure_out()

                via: List[str] = list(msg.get("via", []))
                via.append(name)
                msg["via"] = via
                msg["relayed_at_ms"] = ts_ms()

                assert out_writer is not None
                out_writer.write(jdump(msg))
                await out_writer.drain()
                eprint(f"[{name}] forwarded {msg.get('src')} -> {msg.get('dst')} via={len(via)} hops")
        except Exception as ex:
            eprint(f"[{name}] relay error: {ex}")
        finally:
            try:
                in_writer.close()
                await in_writer.wait_closed()
            except Exception:
                pass
            if out_writer is not None:
                try:
                    out_writer.close()
                    await out_writer.wait_closed()
                except Exception:
                    pass
            eprint(f"[{name}] inbound closed: {peer}")

    server = await asyncio.start_server(handle_inbound, host=listen_host, port=listen_port)
    async with server:
        await server.serve_forever()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MRN terminal node demo")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_router = sub.add_parser("router", help="router/node (recommended multi-client demo)")
    p_router.add_argument("--name", required=True)
    p_router.add_argument("--host", default="127.0.0.1")
    p_router.add_argument("--port", type=int, required=True)
    p_router.add_argument("--delay-ms", type=float, default=0.0)
    p_router.add_argument("--delay-jitter-ms", type=float, default=0.0)
    p_router.add_argument("--loss", type=float, default=0.0, help="baseline drop probability (0..1)")
    p_router.add_argument("--loss-at-max", type=float, default=0.25, help="extra drop at max hop distance (0..1)")
    p_router.add_argument(
        "--distance-delay-ms-per-unit",
        type=float,
        default=1.2,
        help="non-linear distance delay factor (ms); long links penalized ~ (d/max)^2",
    )
    p_router.add_argument("--max-hop-distance", type=float, default=60.0, help="auto-route: max distance per hop (requires /pos)")
    p_router.add_argument("--max-hops", type=int, default=4, help="auto-route: max hops allowed")
    p_router.add_argument("--seed", type=int, default=7)

    p_client = sub.add_parser("client", help="client (send/receive; choose receiver by name)")
    p_client.add_argument("--name", required=True)
    p_client.add_argument("--host", default="127.0.0.1")
    p_client.add_argument("--port", type=int, required=True)
    p_client.add_argument("--to", default=None, help="default recipient (optional)")
    p_client.add_argument("--pos", nargs=2, type=float, metavar=("X", "Y"), default=None, help="initial position (auto multi-hop)")

    p_listen = sub.add_parser("listen", help="destination node (server)")
    p_listen.add_argument("--name", required=True)
    p_listen.add_argument("--host", default="127.0.0.1")
    p_listen.add_argument("--port", type=int, required=True)

    p_connect = sub.add_parser("connect", help="source node (client)")
    p_connect.add_argument("--name", required=True)
    p_connect.add_argument("--host", default="127.0.0.1")
    p_connect.add_argument("--port", type=int, required=True)
    p_connect.add_argument("--dst", required=True)

    p_relay = sub.add_parser("relay", help="relay node (middle hop)")
    p_relay.add_argument("--name", required=True)
    p_relay.add_argument("--listen-host", default="127.0.0.1")
    p_relay.add_argument("--listen-port", type=int, required=True)
    p_relay.add_argument("--forward-host", default="127.0.0.1")
    p_relay.add_argument("--forward-port", type=int, required=True)
    p_relay.add_argument("--delay-ms", type=float, default=0.0)
    p_relay.add_argument("--delay-jitter-ms", type=float, default=0.0)
    p_relay.add_argument("--loss", type=float, default=0.0)
    p_relay.add_argument("--seed", type=int, default=7)

    return p


async def amain(argv: List[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "router":
        sim = ChannelSim(
            delay_ms=args.delay_ms,
            delay_jitter_ms=args.delay_jitter_ms,
            loss=args.loss,
            rng=random.Random(args.seed),
        )
        # attach routing knobs to sim (minimal refactor)
        sim.max_hop_distance = args.max_hop_distance  # type: ignore[attr-defined]
        sim.max_hops = args.max_hops  # type: ignore[attr-defined]
        sim.loss_at_max = args.loss_at_max  # type: ignore[attr-defined]
        sim.distance_delay_ms_per_unit = args.distance_delay_ms_per_unit  # type: ignore[attr-defined]
        return await run_router(args.name, args.host, args.port, sim)
    if args.cmd == "client":
        initial_pos = (float(args.pos[0]), float(args.pos[1])) if args.pos is not None else None
        return await run_client(args.name, args.host, args.port, default_to=args.to, initial_pos=initial_pos)
    if args.cmd == "listen":
        return await run_listen(args.name, args.host, args.port)
    if args.cmd == "connect":
        return await run_connect(args.name, args.host, args.port, args.dst)
    if args.cmd == "relay":
        sim = ChannelSim(
            delay_ms=args.delay_ms,
            delay_jitter_ms=args.delay_jitter_ms,
            loss=args.loss,
            rng=random.Random(args.seed),
        )
        return await run_relay(
            name=args.name,
            listen_host=args.listen_host,
            listen_port=args.listen_port,
            forward_host=args.forward_host,
            forward_port=args.forward_port,
            sim=sim,
        )
    raise AssertionError("unreachable")


def main() -> int:
    try:
        return asyncio.run(amain(sys.argv[1:]))
    except KeyboardInterrupt:
        eprint("stopped")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


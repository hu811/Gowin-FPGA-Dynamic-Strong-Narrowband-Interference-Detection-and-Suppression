"""Exact V19 wire protocol. No signal detection or filter calculations live here."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import struct
from typing import Iterable

PC_MAGIC = bytes.fromhex("d5 49 31 a5")
FPGA_MAGIC = bytes.fromhex("d5 4f 31 a5")
PC_FRAME_BYTES, FPGA_FRAME_BYTES, BLOCK_SAMPLES = 140, 210, 64
RESET, DATA, START, ABORT = range(4)
STATUS, OUTPUT = 0x80, 0x81
RUNNING, COMPLETE, FAULT, START_ACCEPTED = 1, 2, 4, 8
DIAGNOSTIC_U8_SATURATING = 1
FINITE_CAPTURE_20 = 2
KNOWN_FEATURES = DIAGNOSTIC_U8_SATURATING | FINITE_CAPTURE_20
FINITE_CAPTURE_20_MAX_SAMPLES = 1048575
UINT32_MAX_SAMPLES = 0xFFFFFFFF
DIAGNOSTIC_FIELDS = ("numeric", "deadline", "sequence_faults", "saturated")
LINK_FAULT_NAMES = (
    "CRC", "input index", "RX FIFO overflow", "input underflow",
    "output FIFO overflow", "core not ready", "output index mismatch",
    "invalid command/start", "total-count violation", "UART framing", "user abort",
)


def _crc16_table_entry(value: int) -> int:
    for _ in range(8):
        value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


_CRC16_TABLE = tuple(_crc16_table_entry(i) for i in range(256))


def crc16(data: bytes | bytearray | memoryview) -> int:
    # Same reflected0xA001/initialFFFF CRC. Only the exact8bit recurrence is cached.
    value = 0xFFFF
    for byte in data:
        value = (value >> 8) ^ _CRC16_TABLE[(value ^ byte) & 0xFF]
    return value


def encode_command(command: int, index: int = 0, samples: Iterable[int] = ()) -> bytes:
    values = tuple(int(x) for x in samples)
    if command not in (RESET, DATA, START, ABORT):
        raise ValueError("Unknown command")
    if not 0 <= index <= 0xFFFFFFFF:
        raise ValueError("Index/total must fit uint32")
    if command == DATA:
        if not 1 <= len(values) <= BLOCK_SAMPLES:
            raise ValueError("DATA needs 1..64 samples")
    elif values:
        raise ValueError("Control commands cannot contain samples")
    if command in (RESET, ABORT) and index:
        raise ValueError("RESET/ABORT index must be zero")
    if command == START and not index:
        raise ValueError("START total must be positive")
    if any(not -32768 <= x <= 32767 for x in values):
        raise ValueError("Samples must be signed int16")
    packet = bytearray(PC_FRAME_BYTES)
    packet[:4] = PC_MAGIC
    struct.pack_into("<BBI", packet, 4, command, len(values), index)
    if values:
        struct.pack_into(f"<{len(values)}h", packet, 10, *values)
    struct.pack_into("<H", packet, 138, crc16(packet[4:138]))
    return bytes(packet)


@dataclass(frozen=True)
class Snapshot:
    kind: int = STATUS
    count: int = 0
    capacity: int = 4
    flags: int = 0
    first_index: int = 0
    consumed: int = 0
    admitted: int = 0
    commits: int = 0
    observations: int = 0
    dropped: int = 0
    numeric: int = 0
    deadline: int = 0
    sequence_faults: int = 0
    saturated: int = 0
    link_faults: int = 0
    active: int = 0
    error_code: int = 0
    groups: int = 0
    input_level: int = 0
    output_level: int = 0
    max_fir: int = 0
    frequencies: tuple[int, ...] = (0, 0, 0, 0)
    waiting: int = 0
    task_running: int = 0
    samples: tuple[int, ...] = ()
    features: int = 0

    @property
    def diagnostic_counter_encoding(self) -> str:
        return ("uint8_saturating_zero_extended_to_uint32" if self.features & DIAGNOSTIC_U8_SATURATING
                else "uint32_exact")

    @property
    def max_capture_samples(self) -> int:
        return FINITE_CAPTURE_20_MAX_SAMPLES if self.features & FINITE_CAPTURE_20 else UINT32_MAX_SAMPLES

    def row(self) -> dict:
        # Every retained field is immutable scalar; do not deepcopy the64sample
        # payload merely to delete it. Key order/content is unchanged.
        row = {item.name: getattr(self, item.name) for item in fields(self)
               if item.name not in ("samples", "frequencies")}
        row['diagnostic_counter_encoding'] = self.diagnostic_counter_encoding
        row['max_capture_samples'] = self.max_capture_samples
        row.update({name+'_at_least_255':bool(self.features & DIAGNOSTIC_U8_SATURATING and getattr(self,name)>=255)
                    for name in DIAGNOSTIC_FIELDS})
        row.update({f"frequency_{i}_hz": f * 32000 / 2**32
                    for i, f in enumerate(self.frequencies)})
        return row


def decode_frame(packet: bytes | bytearray) -> Snapshot:
    if len(packet) != FPGA_FRAME_BYTES or packet[:4] != FPGA_MAGIC:
        raise ValueError("Not a V19 FPGA frame")
    if crc16(packet[4:208]) != struct.unpack_from("<H", packet, 208)[0]:
        raise ValueError("V19 frame CRC mismatch")
    kind, count, capacity, flags = struct.unpack_from("<4B", packet, 4)
    if kind not in (STATUS, OUTPUT) or capacity not in (3, 4):
        raise ValueError("Invalid frame type/capacity")
    if (kind == STATUS and count != 0) or (kind == OUTPUT and not 1 <= count <= 64):
        raise ValueError("Invalid sample count")
    features = packet[78]
    if flags & ~0x0F or features & ~KNOWN_FEATURES or packet[79] != 0:
        raise ValueError("Reserved V19 bits are nonzero")
    values = struct.unpack_from("<10I", packet, 8)
    if features & DIAGNOSTIC_U8_SATURATING and any(value>255 for value in values[6:10]):
        raise ValueError('Invalid zero-extended saturated diagnostic field')
    if features & FINITE_CAPTURE_20 and (any(value>FINITE_CAPTURE_20_MAX_SAMPLES for value in values[:3])
            or kind==OUTPUT and values[0]+count>FINITE_CAPTURE_20_MAX_SAMPLES):
        raise ValueError('Finite-capture sample index/count exceeds firmware capacity')
    link, active, error, groups, inp, out, fir = struct.unpack_from("<HBB4H", packet, 48)
    frequencies = struct.unpack_from("<4I", packet, 60)
    waiting, task = packet[76], packet[77]
    if groups & ~0x7FFF or waiting > 2 or task > 1 or inp > 512 or out > 512:
        raise ValueError("Invalid V19 status fields")
    return Snapshot(kind, count, capacity, flags, *values, link, active, error,
                    groups, inp, out, fir, frequencies, waiting, task,
                    struct.unpack_from(f"<{count}h", packet, 80) if count else (),features=features)


def encode_snapshot(snapshot: Snapshot) -> bytes:
    """Reference encoder for software/RTL transport tests; not used to fake a board run."""
    if snapshot.count != len(snapshot.samples):
        raise ValueError("Snapshot sample count mismatch")
    packet = bytearray(FPGA_FRAME_BYTES)
    packet[:4] = FPGA_MAGIC
    struct.pack_into("<4B", packet, 4, snapshot.kind, snapshot.count,
                     snapshot.capacity, snapshot.flags)
    struct.pack_into("<10I", packet, 8, snapshot.first_index, snapshot.consumed,
                     snapshot.admitted, snapshot.commits, snapshot.observations,
                     snapshot.dropped, snapshot.numeric, snapshot.deadline,
                     snapshot.sequence_faults, snapshot.saturated)
    struct.pack_into("<HBB4H", packet, 48, snapshot.link_faults, snapshot.active,
                     snapshot.error_code, snapshot.groups, snapshot.input_level,
                     snapshot.output_level, snapshot.max_fir)
    struct.pack_into("<4I", packet, 60, *snapshot.frequencies)
    struct.pack_into("<BB", packet, 76, snapshot.waiting, snapshot.task_running)
    packet[78] = snapshot.features
    if snapshot.count:
        struct.pack_into(f"<{snapshot.count}h", packet, 80, *snapshot.samples)
    struct.pack_into("<H", packet, 208, crc16(packet[4:208]))
    return bytes(packet)


@dataclass
class FrameParser:
    buffer: bytearray = field(default_factory=bytearray)
    crc_errors: int = 0
    format_errors: int = 0
    discarded_bytes: int = 0
    frames: int = 0
    events: list = field(default_factory=list)

    def feed(self, data: bytes) -> list[Snapshot]:
        # Ordered events let RESET synchronization tolerate old partial bytes
        # without hiding a corrupt new-run frame in the same OS read.
        self.events = []
        self.buffer.extend(data)
        result = []
        while True:
            pos = self.buffer.find(FPGA_MAGIC)
            if pos < 0:
                remove = max(0, len(self.buffer) - len(FPGA_MAGIC) + 1)
                self.discarded_bytes += remove
                del self.buffer[:remove]
                break
            self.discarded_bytes += pos
            del self.buffer[:pos]
            if len(self.buffer) < FPGA_FRAME_BYTES:
                break
            packet = self.buffer[:FPGA_FRAME_BYTES]
            if crc16(packet[4:208]) != struct.unpack_from("<H", packet, 208)[0]:
                self.crc_errors += 1
                self.events.append("crc_error")
                self.discarded_bytes += 1
                del self.buffer[0]
                continue
            try:
                decoded = decode_frame(packet)
                result.append(decoded)
                self.events.append(decoded)
            except ValueError:
                self.format_errors += 1
                self.events.append("format_error")
            del self.buffer[:FPGA_FRAME_BYTES]
            self.frames += 1
        return result


class ValidationError(RuntimeError):
    pass


@dataclass
class RunValidator:
    capacity: int
    total: int
    next_output: int = 0
    last_consumed: int = 0
    last_admitted: int = 0
    start_seen: bool = False
    complete_seen: bool = False
    features: int | None = None

    def accept(self, frame: Snapshot, sent: int) -> None:
        if frame.capacity != self.capacity:
            raise ValidationError(f"Capacity mismatch: board C{frame.capacity}, requested C{self.capacity}")
        if self.features is None:self.features=frame.features
        elif frame.features!=self.features:raise ValidationError('Firmware diagnostic features changed during the run')
        if self.total>frame.max_capture_samples:
            raise ValidationError(f'Firmware finite-capture limit is {frame.max_capture_samples} samples '
                                  f'({frame.max_capture_samples/32000:.8f} s); requested {self.total}. '
                                  'No prefill/START or automatic truncation/splitting is allowed.')
        if frame.flags & FAULT or frame.link_faults or frame.error_code:
            faults = [name for bit, name in enumerate(LINK_FAULT_NAMES)
                      if frame.link_faults & (1 << bit)]
            raise ValidationError(f"Board fault: link=0x{frame.link_faults:04x} "
                                  f"({', '.join(faults)}), core=0x{frame.error_code:02x}")
        for name in ("dropped", "numeric", "deadline", "sequence_faults", "saturated"):
            if getattr(frame, name):
                value=getattr(frame,name)
                displayed=(">=255 (saturated diagnostic)" if frame.features & DIAGNOSTIC_U8_SATURATING and name in DIAGNOSTIC_FIELDS and value>=255 else str(value))
                raise ValidationError(f"Board {name} counter is {displayed}")
        if not self.last_consumed <= frame.consumed <= frame.admitted <= sent <= self.total:
            raise ValidationError("Input count/credit discontinuity")
        if frame.admitted < self.last_admitted:
            raise ValidationError("Admitted sample count moved backwards")
        self.last_consumed, self.last_admitted = frame.consumed, frame.admitted
        self.start_seen |= bool(frame.flags & START_ACCEPTED)
        self.complete_seen |= bool(frame.flags & COMPLETE)
        if frame.kind == OUTPUT:
            if frame.first_index != self.next_output:
                raise ValidationError(f"Missing/repeated output: expected {self.next_output}, "
                                      f"received {frame.first_index}")
            if self.next_output + frame.count > self.total:
                raise ValidationError("Too many output samples")
            self.next_output += frame.count

    @property
    def done(self) -> bool:
        return (self.start_seen and self.complete_seen and self.next_output == self.total
                and self.last_consumed == self.total and self.last_admitted == self.total)


def available_credit(sent: int, consumed: int, total: int, limit: int = 384) -> int:
    if not 0 <= consumed <= sent <= total:
        raise ValidationError("Invalid host credit state")
    return min(BLOCK_SAMPLES, total - sent, max(0, limit - (sent - consumed)))

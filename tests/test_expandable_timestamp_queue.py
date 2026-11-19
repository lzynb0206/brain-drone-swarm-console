"""Regression tests for acquisition timestamps surviving processing backlog."""
import unittest
import numpy as np
from src.adapters.real.expandable_eeg_serial_stream import ExpandableEEGSerialStream


def packet(index):
    return bytes((0xA0,index&255))+bytes(30)+bytes((0xC0,))


class FakeSerial:
    def __init__(self,payload):self.payload=payload;self.rx=bytearray()
    @property
    def in_waiting(self):return len(self.rx)
    def write(self,value):
        if value==b'b':self.rx.extend(self.payload)
        return len(value)
    def read(self,count):
        out=bytes(self.rx[:count]);del self.rx[:count];return out
    def reset_input_buffer(self):self.rx.clear()
    def flush(self):pass
    def close(self):pass


class TimestampQueueTest(unittest.TestCase):
    def stream(self,indices,clock,frame_size=32):
        serial=FakeSerial(b''.join(packet(i) for i in indices))
        obj=ExpandableEEGSerialStream(serial_port='TEST',frame_size=frame_size,
            serial_factory=lambda **_:serial,wall_clock=lambda:clock[0],
            settle_seconds=0,startup_timeout_seconds=.05,reset_delay_seconds=0,
            handshake_delay_seconds=0,configure_delay_seconds=0,stream_start_delay_seconds=0)
        obj.connect();self.addCleanup(obj.disconnect);return obj

    def test_batch_tail_not_first_frame_is_anchored_to_arrival(self):
        now=[1000.];stream=self.stream(range(96),now)
        first=stream.read_frame()
        self.assertAlmostEqual(first.sample_timestamps[-1],1000-64/250,places=6)
        self.assertAlmostEqual(first.sample_timestamps[0],1000-95/250,places=6)

    def test_decoder_delay_cannot_retimestamp_queued_samples(self):
        now=[1000.];stream=self.stream(range(96),now)
        first=stream.read_frame();now[0]+=5
        second=stream.read_frame();now[0]+=7
        third=stream.read_frame()
        times=np.r_[first.sample_timestamps,second.sample_timestamps,third.sample_timestamps]
        np.testing.assert_allclose(np.diff(times),1/250,atol=1e-10)
        self.assertAlmostEqual(times[-1],1000,places=6)

    def test_counter_gaps_preserved_in_one_received_batch(self):
        now=[1000.];stream=self.stream([1,2,4],now,frame_size=3)
        times=stream.read_frame().sample_timestamps
        np.testing.assert_allclose(np.diff(times),[.004,.008],atol=1e-10)
        self.assertEqual(stream.health_snapshot()['gap_count'],1)

    def test_capped_serial_reads_and_counter_wrap_preserve_spacing(self):
        now=[1000.];stream=self.stream(range(512),now)
        frames=[stream.read_frame() for _ in range(16)]
        times=np.concatenate([f.sample_timestamps for f in frames])
        self.assertEqual(len(times),512)
        np.testing.assert_allclose(np.diff(times),.004,atol=1e-10)
        self.assertAlmostEqual(times[0],1000-511/250,places=6)
        self.assertAlmostEqual(times[-1],1000,places=6)
        self.assertFalse(stream.health_snapshot()['hardware_synchronized'])

    def test_flush_clears_both_queues(self):
        now=[1000.];stream=self.stream(range(96),now)
        stream.read_frame();self.assertEqual(stream.flush(),64)
        self.assertEqual(len(stream._packet_timestamps),0)
        self.assertEqual(len(stream._packets),0)

if __name__=='__main__':unittest.main()

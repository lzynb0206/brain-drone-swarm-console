"""Read continuous 24-bit BDF and cut explicitly aligned stimulus-log epochs.

Layout reference: https://www.biosemi.com/faq/file_format.htm
No signal-based label inference or assumption that two PC clocks agree.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import shutil
import uuid

import numpy as np

from src.services.external_recording import load_external_segments


class BDFRecording:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        with self.path.open('rb') as handle:
            h = handle.read(256)
            if len(h) != 256 or h[:8] != b'\xffBIOSEMI':
                raise ValueError('不是有效的 24 位 BDF 文件')
            if b'BDF+D' in h[192:236]:
                raise ValueError('暂不支持不连续 BDF+D；不能按连续时间截取')
            self.header_bytes = int(h[184:192])
            count = int(h[252:256])
            self.record_seconds = float(h[244:252])
            records = int(h[236:244])
            if not 1 <= count <= 4096 or self.header_bytes < 256*(count+1):
                raise ValueError('BDF 通道数或文件头长度无效')
            if not math.isfinite(self.record_seconds) or self.record_seconds <= 0:
                raise ValueError('BDF 数据记录时长无效')
            raw = handle.read(count*256)
        if len(raw) != count*256:
            raise ValueError('BDF 文件头不完整')
        fields = {}; offset = 0
        for name, width in [('names',16),('sensor',80),('units',8),('pmin',8),('pmax',8),
                            ('dmin',8),('dmax',8),('prefilter',80),('samples',8),('reserved',32)]:
            fields[name] = tuple(raw[offset+i*width:offset+(i+1)*width].decode('latin1').strip()
                                 for i in range(count))
            offset += count*width
        self.names = fields['names']
        if any(not n for n in self.names) or len(set(self.names)) != count:
            raise ValueError('BDF 通道名缺失或重复，不能按位置猜测电极')
        self.samples = np.array(fields['samples'], dtype=int)
        if np.any(self.samples <= 0):
            raise ValueError('BDF 每记录采样数无效')
        record_bytes = int(self.samples.sum())*3
        size = self.path.stat().st_size - self.header_bytes
        if size <= 0 or size % record_bytes:
            raise ValueError('BDF 数据记录不完整')
        self.record_count = size//record_bytes
        if records != -1 and records != self.record_count:
            raise ValueError('BDF 文件头与实际数据长度不一致')
        self.duration = self.record_count*self.record_seconds
        self._offsets = np.concatenate(([0], np.cumsum(self.samples)*3))
        self._packed = np.memmap(self.path, mode='r', dtype=np.uint8, offset=self.header_bytes,
                                 shape=(self.record_count, record_bytes))
        self.eeg_names = tuple(n for n in self.names if n.lower() not in
                               ('trigger','status','annotations','bdf annotations','edf annotations'))
        if not self.eeg_names:
            raise ValueError('BDF 中没有 EEG 通道')
        indices = [self.names.index(n) for n in self.eeg_names]
        rates = self.samples[indices]/self.record_seconds
        if not np.all(rates == rates[0]):
            raise ValueError('暂不支持 EEG 通道之间采样率不同的 BDF')
        self.fs = float(rates[0])
        self._scale = {}; self._zero = {}
        for i in indices:
            unit = fields['units'][i].lower().replace('µ','u').replace('μ','u')
            multiplier = {'uv':1., 'mv':1000., 'v':1e6}.get(unit)
            if multiplier is None:
                raise ValueError(f'{self.names[i]} 单位 {fields["units"][i]!r} 不明，不能自动视为 μV')
            pmin,pmax,dmin,dmax = (float(fields[k][i]) for k in ('pmin','pmax','dmin','dmax'))
            if not np.isfinite([pmin,pmax,dmin,dmax]).all() or pmin>=pmax or dmin>=dmax:
                raise ValueError('BDF 标定范围无效')
            self._scale[i] = (pmax-pmin)/(dmax-dmin)*multiplier
            self._zero[i] = pmin*multiplier - dmin*self._scale[i]

    def digital(self, index, first_record=0, stop_record=None):
        b = self._packed[first_record:stop_record, self._offsets[index]:self._offsets[index+1]]
        b = np.asarray(b).reshape(-1,3).astype(np.int32)
        value = b[:,0] | b[:,1]<<8 | b[:,2]<<16
        return (value ^ 0x800000) - 0x800000

    def read_samples(self, first, stop):
        if not 0 <= first < stop <= round(self.duration*self.fs):
            raise ValueError('截取范围超出 BDF，未自动裁短')
        per_record = int(round(self.fs*self.record_seconds))
        low, high = first//per_record, (stop+per_record-1)//per_record
        result = []
        for name in self.eeg_names:
            i = self.names.index(name)
            raw = self.digital(i, low, high)[first-low*per_record:stop-low*per_record]
            result.append(raw*self._scale[i]+self._zero[i])
        return np.asarray(result, dtype=np.float32)

    def trigger_summary(self):
        name = next((n for n in self.names if n.lower() in ('trigger','status')), None)
        if name is None:
            return '无 Trigger / Status 通道'
        values = self.digital(self.names.index(name))
        # Report digital changes only; these are not automatically task events.
        return f'{name}：{len(np.unique(values))} 种数字值，{np.count_nonzero(np.diff(values))} 次变化（未解释为任务标签）'


@dataclass(frozen=True)
class ManualAlignment:
    offset_seconds: float
    drift_ppm: float = 0.
    note: str = ''

    def map(self, elapsed):
        if not np.isfinite([self.offset_seconds,self.drift_ppm]).all() or abs(self.drift_ppm)>10000:
            raise ValueError('对齐偏移或时钟漂移无效')
        if not self.note.strip():
            raise ValueError('请记录对齐依据；无同步证据时填写“人工估计，未验证”')
        return self.offset_seconds + float(elapsed)*(1+self.drift_ppm/1e6)


def plan_bdf_segments(recording, log_path, alignment):
    segments = load_external_segments(log_path)
    planned = []
    for context in segments:
        start = alignment.map(context['start_seconds'])
        # Retain the declared capture duration, bounded by actual presentation.
        end_elapsed = min(context['end_seconds'], context['start_seconds']+float(context['duration_seconds']))
        end = alignment.map(end_elapsed)
        first, stop = round(start*recording.fs), round(end*recording.fs)
        if start < 0 or end > recording.duration or stop-first < 32:
            raise ValueError(f'{context["trial_id"]} 对齐后为 {start:.3f}–{end:.3f}s，超出 BDF 或过短；请修正对齐')
        planned.append((context, first, stop))
    return planned


def import_bdf(recording, log_path, output_root, alignment, ssvep_channels, mi_channels):
    planned = plan_bdf_segments(recording, log_path, alignment)
    for title, names in [('SSVEP',ssvep_channels),('MI',mi_channels)]:
        if not names or len(set(names)) != len(names) or any(n not in recording.eeg_names for n in names):
            raise ValueError(f'{title} 通道必须是文件中存在且不重复的电极名称')
    log_path = Path(log_path).resolve()
    token = uuid.uuid4().hex[:10]
    source_run = str(planned[0][0]['run_id'])
    run_id = source_run+'-bdf-'+token
    directory = Path(output_root)/run_id
    directory.mkdir(parents=True, exist_ok=False)
    mapping = {c['trial_id']: c['trial_id']+'-bdf-'+token for c,_,_ in planned}
    paths = []
    try:
        with recording.path.open('rb') as handle:
            bdf_hash = hashlib.file_digest(handle,'sha256').hexdigest()
        log_hash = hashlib.sha256(log_path.read_bytes()).hexdigest()
        for context, first, stop in planned:
            ctx = {**context, 'run_id':run_id, 'source_run_id':source_run,
                   'trial_id':mapping[context['trial_id']], 'montage_id':'external_bdf_named_v1',
                   'channel_names':list(recording.eeg_names), 'ssvep_channel_names':list(ssvep_channels),
                   'mi_channel_names':list(mi_channels), 'recording_mode':'external_bdf',
                   'alignment_status':'manual_unverified', 'hardware_synchronized':False,
                   'alignment_note':alignment.note, 'offset_seconds':alignment.offset_seconds,
                   'drift_ppm':alignment.drift_ppm, 'source_bdf':str(recording.path),
                   'source_bdf_sha256':bdf_hash, 'source_log_sha256':log_hash,
                   'bdf_sample_start':first, 'bdf_sample_stop':stop,
                   'timestamp_domain':'bdf_seconds', 'p_ssvep':None, 'p_ssvep_valid':False,
                   'p_ssvep_is_probability':False, 'physical_display_timing_verified':False}
            ctx['stage'] = context.get('stage') or 'ssvep'
            ctx['paradigm_type'] = 'SSVEP' if ctx['stage']=='ssvep' else 'MI'
            ctx['dedicated_rest_trial_ids'] = {
                key: mapping[value] for key,value in context.get('dedicated_rest_trial_ids',{}).items()
                if value in mapping}
            for key in ('preceding_ssvep_trial_id','baseline_trial_id'):
                ctx[key] = mapping.get(context.get(key),'')
            if context.get('pair_id'):
                ctx['pair_id'] = context['pair_id']+'-bdf-'+token
            ctx['block_id'] = str(context.get('block_id',''))+'-bdf-'+token
            path = directory/(mapping[context['trial_id']]+'.npz')
            # IDs are generated by this application, but still reject path traversal.
            if path.parent != directory or '/' in path.name or '\\' in path.name:
                raise ValueError('非法试次文件名')
            eeg = recording.read_samples(first, stop)
            np.savez_compressed(path, eeg=eeg, fs=recording.fs, channel_names=recording.eeg_names,
                true_label=context['true_label'], trial_context=json.dumps(ctx,ensure_ascii=False),
                metadata=json.dumps(ctx,ensure_ascii=False), subject_id=context.get('acquisition_subject_id',''),
                sample_timestamps=np.arange(first,stop,dtype=float)/recording.fs,
                signal_quality=json.dumps({'status':'unassessed','p_ssvep_valid':False,
                                           'timing_ok':False,'alignment_status':'manual_unverified'}),
                start_time='', units='uV')
            paths.append(path)
        manifest = {'schema_version':1,'source_bdf':str(recording.path),'source_bdf_sha256':bdf_hash,
                    'source_log':str(log_path),'source_log_sha256':log_hash,'run_id':run_id,
                    'alignment':{'status':'manual_unverified','offset_seconds':alignment.offset_seconds,
                                 'drift_ppm':alignment.drift_ppm,'note':alignment.note},
                    'sampling_rate_hz':recording.fs,'all_eeg_channels':recording.eeg_names,
                    'ssvep_channels':list(ssvep_channels),'mi_channels':list(mi_channels),
                    'files':[p.name for p in paths],'created_at':datetime.now().isoformat()}
        (directory/'import_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        shutil.copyfile(log_path, directory/'events.jsonl')
    except Exception:
        # Only this newly-created import directory is rolled back, never input files.
        shutil.rmtree(directory)
        raise
    return paths

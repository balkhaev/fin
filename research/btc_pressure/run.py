"""Replay recorded raw messages or fit a strictly prior, empirical paper gate."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import mean
from .adapters import Event,Normalizer
from .strategy import Features,Mechanisms
from .paper import Broker,Settings,model_fingerprint


def write(path,data):
    path.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')


def replay(root:Path,out:Path,mode='observe',venue='bybit_perp',calibration=None):
    if out.exists():raise FileExistsError('Use a new output directory; evidence is not overwritten')
    manifest=json.loads((root/'manifest.json').read_text())
    if calibration and calibration.get('training_end_ms',math.inf)>=manifest['start_ms']:
        raise ValueError('Calibration overlaps replay period')
    raw=root/'raw.jsonl.gz'
    if hashlib.sha256(raw.read_bytes()).hexdigest()!=manifest['raw_sha256']:
        raise ValueError('Raw capture checksum mismatch')
    if manifest.get('schema')!='btc-pressure-raw-v1':raise ValueError('Wrong capture schema')
    normalizer=Normalizer();features=Features(venue);mechanisms=Mechanisms()
    broker=Broker(venue=venue,mode=mode,calibration=calibration)
    counts,blocked=Counter(),Counter();errors=[];last_second=-1;frames=0;lines=0
    with gzip.open(raw,'rt',encoding='utf-8') as stream:
        for line in stream:
            lines+=1;record=json.loads(line);now=record['received_ms']
            try:
                events=normalizer.feed(record)
                for event in events:
                    counts[event.source+':'+event.kind]+=1
                    features.add(event);broker.on_event(event)
                    if event.kind=='gap':mechanisms.reset()
            except (ValueError,KeyError,TypeError) as exc:
                errors.append(dict(seq=record.get('seq'),error=str(exc)))
                broker.halted=True;broker.incomplete=True
                event=Event(now,now,venue,'gap',{'reason':'parse_or_sequence_error'})
                features.add(event);broker.on_event(event);mechanisms.reset()
                if normalizer.last_seq!=record.get('seq'):break
                continue
            if now//1000==last_second:continue
            last_second=now//1000
            frame,reason=features.frame(now);blocked[reason]+=1
            if not frame:
                if broker.pending:broker.cancel('feature_quality_lost')
                if broker.position:broker.request_exit('feature_quality_lost')
            if frame:
                frames+=1;broker.manage_frame(frame)
                proposal=mechanisms.on_frame(frame)
                if proposal:broker.propose(proposal)
    if lines!=manifest['records']:
        broker.incomplete=True;errors.append(dict(error='Manifest record count mismatch'))
    report=broker.report()
    complete=(frames>0 and not errors and not manifest.get('errors') and not report['execution_incomplete']
              and manifest.get('synthetic') is False and not report['open_position_at_end']
              and not report['pending_entry'] and report['funding_time_drawdown_verified'])
    report['performance_evaluable']=bool(complete)
    report['strategy_return_pct']=report['marked_return_pct'] if complete else None
    report.update(schema='btc-pressure-result-v1',raw_sha256=manifest['raw_sha256'],
                  input_start_ms=manifest['start_ms'],input_end_ms=manifest['end_ms'],
                  input_records=lines,normalized_counts=dict(counts),feature_status=dict(blocked),
                  usable_feature_frames=frames,parse_errors=errors,source_errors=manifest.get('errors',[]),
                  synthetic=manifest.get('synthetic'),market_capture_only=manifest.get('collection_only',False),
                  annual_test_complete=False,same_venue_execution_model_validated=False,
                  limitations=['Short recorded stream, not historical annual backtest.',
                    'Paper fills use displayed depth and conservative queue; no actual exchange fills.',
                    'No private order/cancel acknowledgements or liquidation/margin engine.',
                    'Recorder mark funding is prediction, never assumed realized payment.',
                    '500% annual target is not established; CAGR intentionally null.',
                    'Experimental defaults are not user-confirmed account constraints.'])
    out.mkdir(parents=True)
    write(out/'report.json',report)
    write(out/'trades.json',report['closed_trades'])
    print(json.dumps({k:v for k,v in report.items() if k not in ('events','closed_trades')},indent=2))
    return report


def fit_gate(reports,out:Path):
    """Whole UTC-day bootstrap; diagnostic, not selection-corrected market proof."""
    if out.exists():raise FileExistsError(out)
    source=[json.loads(path.read_text()) for path in reports]
    if not source:raise ValueError('No training reports')
    identity=(source[0]['venue'],source[0]['settings_sha256'])
    periods=sorted((r['input_start_ms'],r['input_end_ms']) for r in source)
    if any(a[1]>b[0] for a,b in zip(periods,periods[1:])):raise ValueError('Overlapping training captures')
    groups=defaultdict(lambda:defaultdict(list));end=0;hashes=[]
    for r in source:
        if r.get('synthetic') is not False or r.get('mode')!='diagnostic' or r.get('execution_incomplete') or r.get('parse_errors') or r.get('source_errors'):
            raise ValueError('Training requires intact real diagnostic evidence')
        if r.get('open_position_at_end') or r.get('pending_entry'):raise ValueError('Unresolved exposure in training')
        if r.get('model_sha256')!=model_fingerprint() or r.get('performance_evaluable') is not True:
            raise ValueError('Training model or performance evidence is not current and complete')
        if (r['venue'],r['settings_sha256'])!=identity:raise ValueError('Mixed contract/cost identity')
        end=max(end,r['input_end_ms']);hashes.append(r['raw_sha256'])
        for t in r['closed_trades']:
            if t['exit_ms']>r['input_end_ms'] or t['entry_ms']<r['input_start_ms']:raise ValueError('Trade outside evidence period')
            x=float(t['net_r'])
            if not math.isfinite(x):raise ValueError('Nonfinite training return')
            groups[f"{t['family']}:{t['side']}"][t['exit_ms']//86400000].append(x)
    cells={};rng=random.Random(20260905)
    for key,days in sorted(groups.items()):
        means=[mean(x) for x in days.values()]
        bootstrap=sorted(mean(rng.choices(means,k=len(means))) for _ in range(2000))
        cells[key]=dict(trades=sum(map(len,days.values())),days=len(days),
                        mean_daily_r=mean(means),lower_mean_daily_r=bootstrap[49])
    result=dict(schema='btc-pressure-gate-v2',model_sha256=model_fingerprint(),venue=identity[0],settings_sha256=identity[1],
                training_end_ms=end,source_hashes=hashes,synthetic=False,cells=cells,
                statistic='2.5% bootstrap bound of day-mean net R; uncorrected exploratory estimate',
                live_ready=False)
    write(out,result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    replay_parser=sub.add_parser('replay')
    replay_parser.add_argument('--data',type=Path,required=True);replay_parser.add_argument('--out',type=Path,required=True)
    replay_parser.add_argument('--mode',choices=['observe','diagnostic','calibrated'],default='observe')
    replay_parser.add_argument('--venue',choices=['bybit_perp','binance_perp'],default='bybit_perp')
    replay_parser.add_argument('--calibration',type=Path)
    fit=sub.add_parser('fit-gate');fit.add_argument('--reports',type=Path,nargs='+',required=True);fit.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.command=='fit-gate':fit_gate(args.reports,args.out)
    else:
        calibration=json.loads(args.calibration.read_text()) if args.calibration else None
        replay(args.data,args.out,args.mode,args.venue,calibration)

if __name__=='__main__':main()

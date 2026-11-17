"""Reproduce four execution contracts on isolated PostgreSQL, without paid calls."""
import argparse
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'src'))
CASES=[
    ('delegation','转授权不能越界',[
        'test_kernel.KernelTests.test_delegation_cannot_expand_actions_or_expiry',
        'test_kernel.KernelTests.test_authorization_revocation_cascades_to_delegated_workers']),
    ('acceptance','修订稿不能复用旧验收',[
        'test_kernel.KernelTests.test_stale_artifact_review_rejected_after_rework']),
    ('unknown_result','未知结果不自动重发',[
        'test_runtime.RuntimeTests.test_transport_failure_never_automatically_replays',
        'test_runtime.RuntimeTests.test_restart_marks_unknown_and_keeps_payer_reservation']),
    ('settlement','重复回执只结算一次',[
        'test_runtime.RuntimeTests.test_real_adapter_settles_once_and_delivers',
        'test_runtime.RuntimeTests.test_receipt_recovers_settlement_after_interruption']),
]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='New JSON report path; existing evidence is never overwritten')
    args=parser.parse_args()
    if args.output.exists():parser.error('Output already exists')
    rows=[]
    for key,label,names in CASES:
        suite=unittest.TestLoader().loadTestsFromNames(names)
        started=time.monotonic()
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        passed=result.wasSuccessful() and not result.skipped and result.testsRun==len(names)
        rows.append(dict(contract=key,label=label,passed=passed,tests=result.testsRun,skipped=len(result.skipped),
            failures=len(result.failures),errors=len(result.errors),seconds=round(time.monotonic()-started,4),test_ids=names))
        print(('PASS' if passed else 'FAIL')+' '+label)
    report=dict(format='orchpact-contract-verification',storage='isolated-postgresql',provider_transport='synthetic',
        quality_evidence=False,scope='selected-behavior-regressions',passed=all(row['passed'] for row in rows),results=rows)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()

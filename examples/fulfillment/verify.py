"""Independent, bounded feasibility checks. Does not read the reference solution."""
import json
from pathlib import Path

from orchpact.scenarios.fulfillment import check_plan

HERE=Path(__file__).resolve().parent


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=HERE/'input.json')
    parser.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args();result=check_plan(json.loads(args.input.read_text()),json.loads(args.candidate.read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='PASS' else 2 if result['status']=='FAIL' else 3)

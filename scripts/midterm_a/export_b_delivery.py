"""Export all acquired real contribution/code snapshots for B; no archive or Git writes."""
import argparse
import json
from pathlib import Path
from oss_mentor.datasets.b_delivery import export_delivery


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    result=export_delivery(args.coverage,args.output,Path(__file__).with_name('verify_b_delivery.py'))
    print(json.dumps({'dataset_version':result['dataset_version'],'entity_counts':result['entity_counts'],'quality':result['quality']},ensure_ascii=False))

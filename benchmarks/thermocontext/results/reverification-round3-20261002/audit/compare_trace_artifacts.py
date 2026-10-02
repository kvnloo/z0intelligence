"""Compare saved trace artifacts only; never run a sampler."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--prior',type=Path,required=True);p.add_argument('--rerun',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 prior={str(p.relative_to(a.prior)):p for d in ('traces','graphs') for p in (a.prior/d).rglob('*') if p.is_file()}
 rerun={str(p.relative_to(a.rerun)):p for d in ('traces','graphs') for p in (a.rerun/d).rglob('*') if p.is_file()}
 differences=[];content_differences=[]
 for rel in sorted(prior.keys() & rerun.keys()):
  if digest(prior[rel])!=digest(rerun[rel]):
   differences.append(rel)
   if prior[rel].suffix=='.npz':
    with np.load(prior[rel],allow_pickle=False) as old,np.load(rerun[rel],allow_pickle=False) as new:
     if set(old.files)!=set(new.files) or any(not np.array_equal(old[key],new[key]) for key in old.files):content_differences.append(rel)
   elif prior[rel].read_bytes()!=rerun[rel].read_bytes():content_differences.append(rel)
 result={'schema':'thermocontext.repeated_seed_artifact_comparison.v1','prior_files':len(prior),'rerun_files':len(rerun),
  'identical_file_sets':prior.keys()==rerun.keys(),'all_artifact_sha256_equal':prior.keys()==rerun.keys() and not differences,
  'hash_differences':differences,'decoded_array_differences':content_differences,
  'all_decoded_trace_arrays_equal':prior.keys()==rerun.keys() and not content_differences,
  'comparison_scope':'traces and auxiliary graphs; row timings/provenance intentionally excluded',
  'interpretation':'Repeated-seed reproducibility, not a new independent trial',
  'no_new_sampling':True,'script_sha256':digest(Path(__file__))}
 with a.out.open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
 print(json.dumps({k:v for k,v in result.items() if k not in ('hash_differences','decoded_array_differences')}))
if __name__=='__main__':main()

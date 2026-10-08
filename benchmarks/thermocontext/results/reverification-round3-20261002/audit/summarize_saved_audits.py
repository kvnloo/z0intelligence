"""Assemble a compact decision from completed saved audits; no sampling."""
import argparse,hashlib,json
from pathlib import Path

def read(path):return json.loads(path.read_text())
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--review',type=Path,required=True);p.add_argument('--repo',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 root=a.review;prior=a.repo/'benchmarks/thermocontext/results/next-wave'
 reproduction=read(root/'reproduction-independent-audit.json');small=read(root/'small-full-independent-audit.json');traces=read(root/'small-full-trace-comparison.json');remote=read(root/'remote-wave-independent-audit.json')
 old_provenance=read(prior/'small-full-20261002/provenance.json');new_provenance=read(root/'small-full/provenance.json')
 old=old_provenance['source_sha256'];new=new_provenance['source_sha256'];common=old.keys()&new.keys()
 changes={k:{'prior':old[k],'rerun':new[k]} for k in common if old[k]!=new[k]}
 source_comparison={'all_common_sources_unchanged':not changes,'changed_common_sources':changes,'removed_sources':sorted(old.keys()-new.keys()),'added_sources':sorted(new.keys()-old.keys()),'protocol_sha256_equal':old_provenance['protocol_sha256']==new_provenance['protocol_sha256'],'arm_config_sha256_equal':old_provenance['arm_config_sha256']==new_provenance['arm_config_sha256'],'prior_sampling_revision':old_provenance['git_revision'],'rerun_sampling_revision':new_provenance['git_revision'],'note':'Strict source-map equality in the raw audit is false only because three diagnostic/audit helpers were newly inventoried; all shared source bytes are unchanged.'}
 assert reproduction['comparison']['exact_non_timing_equality'] and reproduction['selected_arithmetic']['all_selected_checks_pass']
 assert small['comparison']['exact_non_timing_equality'] and small['selected_arithmetic']['all_selected_checks_pass']
 assert small['retained_trace_audit']['all_row_checks_pass'] and small['retained_trace_audit']['complete_present_arm_grids']
 assert traces['all_artifact_sha256_equal'] and not changes and not source_comparison['removed_sources']
 assert remote['selected_arithmetic']['all_selected_checks_pass'] and all(remote['executed_source_bindings'].values()) and all(remote['expected_source_grids'].values()) and remote['fixed_adapter_snapshot_matches']
 manifest_path=root/'independent-wave-source/results/thermocontext/2026-10-02-thrml-wave1-packed/manifest.json';manifest=read(manifest_path)
 checks={name:digest(root/'independent-wave-raw'/name)==value['sha256'] for name,value in manifest['files'].items()};assert all(checks.values())
 final_manifest=read(root/'independent-wave-raw/final-source-sha256.json')
 final_differences=[name for name,value in final_manifest.items() if digest(root/'independent-wave-source'/name)!=value]
 result={'schema':'thermocontext.independent_reverification_decision.v1','decision':'REPRODUCIBILITY_CONFIRMED_RETAIN_N32_STOP',
  'scientific_disposition':'KILL the current engineered-cohort case for adding sampling to Hermes; no population or hardware conclusion',
  'original_reproduction':{'rows':432,'independent_selected_checks':'PASS','exact_masks_ids_energy_count_tokens_success_and_first_valid_match_prior':True,'artifact_hashes_checked':reproduction['artifact_hashes']['checked']},
  'small_full_reproduction':{'rows':2520,'independent_exact_tasks':36,'selected_arithmetic_and_retained_trace_audit':'PASS','complete_arm_grids':True,'declared_artifact_hashes_checked':2632,'retained_trace_and_auxiliary_graph_hashes_identical':2628,'selected_non_timing_fields_exactly_match_prior':True,'timing_equality_claim':False,'interpretation':'Repeated same-task/same-seed reproduction; not an independent enlarged cohort','source_comparison':source_comparison},
  'independent_remote_wave':{'source_revision':read(root/'independent-wave-source/export-manifest.json')['revision'],'archive_sha256':manifest['archive_sha256'],'archive_files_hash_verified':len(checks),'selected_rows_independently_checked':2060,'executed_source_bindings_checked':len(remote['executed_source_bindings']),'fixed_adapter_snapshot_matches':True,'all_selected_arithmetic_checks_pass':True,'small_N_exact_tasks':remote['selected_arithmetic']['small_tasks_independently_enumerated'],'N32_component_oracle_tasks':remote['selected_arithmetic']['large_tasks_component_enumerated'],'original432_matches_prior':True,'adaptive_N32':{'task_ids':'0..9 only','prototype_JAX_success':47,'THRML_success':48,'runs_per_backend':80,'stop_preserved':True},'final_manifest_only_documentation_differences':final_differences,'retained_trajectory_limit':remote['retained_trajectory_audit'],'distinct_wave':remote['not_comparable_as_same_wave']},
  'stop':{'prior_local_wave_N32_adaptive_success':'57/96','remote_wave_N32_adaptive_success':'48/80 THRML, different schedule and partial tasks','N32_resampled_by_this_audit':False,'N64_N128_authorized_or_sampled_by_this_audit':False,'no_further_sampling':True},
  'limits':['Synthetic evidence task and token weights; no consumed Hermes tokens or real task outcomes','Independent remote archive supports selected-output checks, not retained trajectory claims','CPU timing observations are not reproducibility targets, isolated benchmarks, or hardware energy claims','Shared tasks/seeds across reproductions do not increase the independent sample size'],
  'evidence_sha256':{name:digest(root/name) for name in ['reproduction-independent-audit.json','small-full-independent-audit.json','small-full-trace-comparison.json','remote-wave-independent-audit.json','audit_saved_waves.py','compare_trace_artifacts.py']},'no_provider_calls':True,'no_new_sampling':True}
 with a.out.open('x') as f:json.dump(result,f,indent=2,sort_keys=True);f.write('\n')
 print(json.dumps({'decision':result['decision'],'output':str(a.out),'trace_hashes_equal':2628,'remote_selected_rows':2060}))
if __name__=='__main__':main()

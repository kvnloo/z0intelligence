"""Versioned, observe-only pipeline dispatch over frozen training categories."""
import hashlib
import json
import time

from .slm_pipeline import pipeline
from .slm_methods import predict_method
from .shadow_binary import _context, shortlist_tools


def scope_digest(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_pipeline_config(config):
    if config.get('decision_method') != 'slm_pipeline_v1':
        raise ValueError('invalid_pipeline_method')
    if config.get('first','binary') not in ('binary','bt') or config.get('final','bt') not in ('binary','bt','threeway'):
        raise ValueError('invalid_pipeline_stage')
    groups=config.get('frozen_categories')
    if not isinstance(groups,list) or not 1<=len(groups)<=8 or any(not isinstance(g,list) or not g for g in groups):
        raise ValueError('invalid_frozen_categories')
    names=[v for group in groups for v in group]
    if any(not isinstance(v,str) or not v for v in names) or len(names)!=len(set(names)):
        raise ValueError('invalid_frozen_vocabulary')
    if not isinstance(config.get('training_digest'),str) or not config['training_digest']:
        raise ValueError('missing_training_identity')
    return names


def run_scoped(payload, legal, backend, config, *, shortlist=(),
               n_ctx=4096, policy_identity=None, policy_home=None):
    started=time.monotonic()
    digest=scope_digest(config)
    row={'label':'slm-method','arm':'slm-method:'+digest,'arm_scope':digest,
         'family':'omp.tool','selected_action':None,'abstained':True,
         'backend':getattr(backend,'backend_id',None),
         'model':getattr(getattr(backend,'config',None),'model',None),
         'method_config':config,'executed_action':None}
    calls=[]
    try:
        method=config.get('decision_method')
        if method not in ('slm_pipeline_v1','slm_threeway_v1','slm_bt_v1','semantic_hierarchy_binary_v1'):
            raise ValueError('unrecognized_scoped_method')
        send=getattr(getattr(backend,'_transport',None),'chat',None)
        if not callable(send):
            raise ValueError('backend_unavailable')
        def chat(*args, **kwargs):
            call_started=time.monotonic()
            accounting={'tokens':0,'failed':True}
            try:
                outcome=send(*args,**kwargs)
                accounting.update(tokens=outcome.usage.get('total_tokens',0),failed=False)
                return outcome
            finally:
                accounting['wall_ms']=1000*(time.monotonic()-call_started)
                calls.append(accounting)
        if policy_identity is not None and policy_home is not None:
            from .choice_capacity import effective_cap
            cap=effective_cap(.6,policy_identity,policy_home)
            required=3 if method in ('slm_threeway_v1','semantic_hierarchy_binary_v1') or config.get('final')=='threeway' else 2
            if cap<required:
                raise ValueError('option_capacity_insufficient')
        context,_=_context(payload)
        actual_model = row.get('model')
        for stage in ('first_model', 'final_model'):
            if config.get(stage) and config[stage] != actual_model:
                raise ValueError('stage_model_not_bound_to_backend')
        if method=='slm_pipeline_v1':
            validate_pipeline_config(config)
            groups=config['frozen_categories']
            legal_ids=set(legal.ids())
            # Frozen membership remains part of scope; the live legality filter
            # cannot add a tool or change its trained category.
            groups=[[v for v in group if v in legal_ids] for group in groups]
            groups=[group for group in groups if group]
            answer=pipeline(json.dumps(context),groups,chat,chat,
                            first=config.get('first','binary'),final=config.get('final','bt'),
                            margin=float(config.get('margin',.25)))
        else:
            choices=shortlist_tools(payload,legal,shortlist,3)
            selected_method = {'slm_threeway_v1':'tournament3',
                               'semantic_hierarchy_binary_v1':'hierarchical'}.get(method,'bt')
            answer=predict_method(json.dumps(context),choices,chat,
                                  method=selected_method,n_ctx=n_ctx)
        row.update(answer)
        if row.get('selected_action') not in set(legal.ids()):
            row.update(selected_action=None,abstained=True)
        return row
    except Exception as exc:
        row.update(error=type(exc).__name__+': '+str(exc),selected_action=None,abstained=True)
        return row
    finally:
        row['latency_ms']=1000*(time.monotonic()-started)
        row['rpc_count']=len(calls)
        row['failed_rpc_count']=sum(c['failed'] for c in calls)
        row['tokens']=sum(c['tokens'] for c in calls)

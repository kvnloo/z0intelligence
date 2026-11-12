import hashlib
from z0int.worker_activity import project_activity, render_activity, acknowledge_adoption
from z0int.receipt import append_receipt
from test_worker_activity import row
import pytest


def test_distribution_and_adoption_do_not_double_count():
    a=row(status='incomplete',input_tokens=3,output_tokens=2)
    a['extra']['physical_call_attempted']=True
    b=row('b',index=1,input_tokens=5,output_tokens=4)
    b['provider']='winner';b['extra'].update(physical_call_attempted=True,parent_consumption='adopted',subtask='label\x1b[2J')
    snap=project_activity([a,b,b])
    assert snap['aggregate']['attempted_providers']=={'test':1,'winner':1}
    assert snap['aggregate']['adopted_providers']=={'winner':1}
    assert snap['aggregate']['known_input_tokens']==8
    text=render_activity(snap)
    assert '\x1b' not in text
    assert 'test:incomplete → winner:completed' in text
    assert 'adopted: winner' in text


def test_adoption_requires_matching_completed_output(tmp_path):
    a=row();a['extra']['output_sha256']=hashlib.sha256(b'answer').hexdigest()
    append_receipt(a,root=tmp_path)
    with pytest.raises(ValueError):acknowledge_adoption('a','wrong','parent',root=tmp_path)
    with pytest.raises(ValueError):acknowledge_adoption('a','answer','other',root=tmp_path)
    ack=acknowledge_adoption('a','answer','parent',root=tmp_path)
    assert ack['extra']['parent_consumption']=='adopted'


def test_completion_alone_is_not_adoption():
    snap=project_activity([row()])
    assert not snap['aggregate']['adopted_providers']
    assert 'adopted: not recorded' in render_activity(snap)

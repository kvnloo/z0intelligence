from z0int import claude_code_skills as cs

SKILLS = {'hyperframes': 'Make or render a video composition', 'media-use': 'Media for HyperFrames projects',
          'pstack-qa': 'QA test a web application in a headless browser', 'agent-reach': 'research reddit twitter'}


def test_family_is_semantic():
    assert cs.family('media-use', SKILLS['media-use']) == 'hyperframes'
    assert cs.family('pstack-qa') == 'pstack' and cs.family('agent-reach') == 'agent-reach'


def test_whole_family_exposed_and_rest_user_invocable():
    exposed, hidden, _ = cs.select('render the video', SKILLS)
    assert exposed == ['hyperframes', 'media-use'] and hidden == ['agent-reach', 'pstack-qa']
    assert cs.settings('render the video', SKILLS)['skillOverrides'] == {'agent-reach': 'user-invocable-only', 'pstack-qa': 'user-invocable-only'}


def test_named_skill_is_always_exposed():
    assert 'agent-reach' in cs.select('use /agent-reach for this', SKILLS)[0]

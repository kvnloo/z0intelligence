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


class _Fake:
    """DecisionBackend stand-in: P(yes)=0.9 when the question's family label is in `hot`."""

    def __init__(self, hot=(), fail=False):
        self.hot, self.fail, self.seen = set(hot), fail, []

    def evaluate(self, request):
        from z0int.backends.base import DecisionAnswer, DecisionResult
        if self.fail:
            raise RuntimeError('no model')
        self.seen.append(request)
        answers = []
        for q in request.questions:
            p = 0.9 if any(f'the {h} skills' in q.instructions for h in self.hot) else 0.01
            answers.append(DecisionAnswer(q.id, 'boolean', {'false': 1 - p, 'true': p}, p >= 0.5))
        return DecisionResult('fake', None, None, tuple(answers), 0.0)


def test_semantic_exposes_whole_family_over_threshold():
    exposed, hidden, why = cs.select_semantic('make a promo', SKILLS, threshold=0.5, backend=_Fake({'hyperframes'}))
    assert exposed == ['hyperframes', 'media-use'] and hidden == ['agent-reach', 'pstack-qa']
    assert why['hyperframes'].startswith('p=')


def test_semantic_one_question_per_family_and_skill_mode_max():
    fake = _Fake({'pstack-qa'})
    exposed, _, _ = cs.select_semantic('qa it', SKILLS, threshold=0.5, backend=fake, mode='skill')
    assert exposed == ['pstack-qa'] and len(fake.seen[0].questions) == len(SKILLS)
    fake = _Fake()
    cs.select_semantic('x', SKILLS, backend=fake)
    assert len(fake.seen[0].questions) == 3  # hyperframes, pstack, agent-reach


def test_semantic_abstains_to_expose_all_on_backend_failure():
    exposed, hidden, why = cs.select_semantic('anything', SKILLS, backend=_Fake(fail=True))
    assert exposed == sorted(SKILLS) and hidden == [] and '*' in why


def test_hybrid_named_rule_overrides_low_probability():
    exposed, _, why = cs.select_semantic('use /agent-reach', SKILLS, threshold=0.5, backend=_Fake())
    assert exposed == ['agent-reach'] and why['agent-reach'] == 'named'
    assert cs.select_semantic('use /agent-reach', SKILLS, threshold=0.5, backend=_Fake(), hybrid=False)[0] == []

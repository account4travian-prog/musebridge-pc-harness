from musebridge.headers import HeaderManager,format_header,fingerprint


def test_headers_commit_only_after_ack_and_repeat_on_real_scope_changes():
    manager=HeaderManager()
    assert manager.needs_header('a','one') and manager.needs_header('a','one')
    manager.acknowledge('a','one',True)
    assert not manager.needs_header('a','one')
    assert manager.needs_header('a','new') and manager.needs_header('b','one')
    manager.reset();assert manager.needs_header('a','one')


def test_repeat_every_eight_is_turn_one_nine_seventeen():
    manager=HeaderManager();manager.repeat_every=8
    actual=[]
    for turn in range(1,19):
        header=manager.needs_header('a','same')
        if header:actual.append(turn)
        manager.acknowledge('a','same',header)
    assert actual == [1,9,17]


def test_header_unicode_paths_cannot_spill_the_budget_or_add_instruction_lines():
    header=format_header('main',{'name':'\u8449'*400+'\nInjected','root':'C:/'+ '\u8449'*2000},
        {'profile':'desktop','paused':False},{'powershell':{'version':'5.1'},'bash':{'available':True}},
        {'branch':'branch','status_summary':'3 changed files'},['file.read','shell.run','browser.act'],1)
    assert len(header.encode()) < 800 and len(header.splitlines()) <= 8
    assert 'bridge.context' in header and 'scope_revision' not in header and 'run_id' not in header


def test_pause_generation_and_project_identity_are_part_of_header_fingerprint():
    base=fingerprint('main',{'id':'a'},{'profile':'project','paused':False},0,['file.read'],1)
    assert base!=fingerprint('main',{'id':'a'},{'profile':'project','paused':False},2,['file.read'],1)
    assert base!=fingerprint('main',{'id':'b'},{'profile':'project','paused':False},0,['file.read'],1)

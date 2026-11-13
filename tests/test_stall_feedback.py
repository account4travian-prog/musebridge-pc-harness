from test_finish_line import harness, begin, write


def test_stall_hint_is_once_on_sixteenth_call_and_never_blocks(harness):
    _, _, router, cid, _ = harness
    begin(router,cid)
    for _ in range(15):
        result=router.call('file.list',{'conversation_id':cid})
        assert result['ok'] and '15 calls without progress' not in str(result)
    result=router.call('file.list',{'conversation_id':cid})
    assert result['ok'] and '15 calls without progress' in str(result)
    assert '15 calls without progress' not in str(router.call('file.list',{'conversation_id':cid}))


def test_progress_restarts_stall_window_and_runs_are_isolated(harness):
    _, _, router, cid, _ = harness
    begin(router,cid)
    for _ in range(12):router.call('file.list',{'conversation_id':cid})
    write(router,cid)
    for _ in range(15):
        assert '15 calls without progress' not in str(router.call('file.list',{'conversation_id':cid}))
    assert '15 calls without progress' in str(router.call('file.list',{'conversation_id':cid}))
    begin(router,cid)
    assert '15 calls without progress' not in str(router.call('file.list',{'conversation_id':cid}))

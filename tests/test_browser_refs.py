import json
import pytest

from test_browser import local_site
from musebridge.browser import BrowserTools
from musebridge.browser_refs import StaleRef


def ref(result,role):
    return next(item['ref'] for item in result['controls'] if item['role']==role)


def test_ref_only_form_diff_stale_and_conversation_isolation(tmp_path,local_site):
    browser=BrowserTools(tmp_path)
    try:
        first=browser.call('browser.navigate',{'conversation_id':'a','url':local_site})
        entry=ref(first,'textbox')
        save=ref(first,'button')
        filled=browser.call('browser.act',{'conversation_id':'a','ref':entry,'action':'fill','text':'saved via refs'})
        assert filled['snapshot_kind']=='diff'
        clicked=browser.call('browser.act',{'conversation_id':'a','ref':save,'action':'click'})
        assert 'saved via refs' in str(clicked['changes'])
        assert 'initial' in str(clicked['changes'])
        browser.call('browser.snapshot',{'conversation_id':'a'})
        with pytest.raises(StaleRef):browser.call('browser.act',{'conversation_id':'a','ref':entry,'action':'fill','text':'wrong'})
        browser.call('browser.navigate',{'conversation_id':'b','url':local_site})
        with pytest.raises(StaleRef):browser.call('browser.act',{'conversation_id':'b','ref':save,'action':'click'})
    finally:browser.close()


def test_removed_element_is_refused_and_eval_is_eight_kb(tmp_path,local_site):
    browser=BrowserTools(tmp_path)
    try:
        first=browser.call('browser.navigate',{'conversation_id':'a','url':local_site})
        save=ref(first,'button')
        browser.call('browser.eval',{'conversation_id':'a','js':'document.querySelector("button").remove()'})
        with pytest.raises(StaleRef):browser.call('browser.act',{'conversation_id':'a','ref':save,'action':'click'})
        large=browser.call('browser.eval',{'conversation_id':'a','js':'"x".repeat(20000)'})
        assert large['truncated'] and len(json.dumps(large))<8000
    finally:browser.close()


def test_large_snapshot_filter_finds_late_element_under_budget(tmp_path,local_site):
    browser=BrowserTools(tmp_path)
    try:
        browser.call('browser.navigate',{'conversation_id':'a','url':local_site})
        browser.call('browser.eval',{'conversation_id':'a','js':'''document.body.innerHTML=Array.from({length:5000},(_,i)=>`<button>Item ${i}</button>`).join('');true'''})
        large=browser.call('browser.snapshot',{'conversation_id':'a'})
        assert large['truncated'] and 'filter' in large['hint']
        narrowed=browser.call('browser.snapshot',{'conversation_id':'a','filter':'Item 4999'})
        assert len(narrowed['controls'])==1
        assert narrowed['controls'][0]['name']=='Item 4999'
        assert len(json.dumps(narrowed).encode())<24000
    finally:browser.close()

"""Synthetic conversational tool feedback and authorization boundaries."""
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace
import pytest
from chat_to_howto.repositories.chat import ChatRepository
from chat_to_howto.repositories.content import ContentRepository
from chat_to_howto.api.session import digest
from chat_to_howto.services.mixed_search import MixedSearchService
from chat_to_howto.providers.base import ProviderResponse
from chat_to_howto.services.conversation_agent import ConversationAgentService

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
SEARCH = {'tool':'search_sources','arguments':{'terms':['创业'],'summary_type':'ideas','goal':'创业想法'}}
class Provider:
    name='fake'
    def __init__(self, actions): self.actions=list(actions); self.requests=[]
    async def generate(self, request, cancellation):
        self.requests.append(request)
        return ProviderResponse(provider='fake',model=request.model,content=json.dumps(self.actions.pop(0),ensure_ascii=False),finish_reason='stop')

def setup(tmp_path, actions, **kwargs):
    repository=ChatRepository(tmp_path/'conversations.sqlite3')
    ContentRepository(repository).save_batch(batch_id='notes',session_digest=digest('owner'),created_at_utc=NOW.isoformat(),images=[],units=[dict(unit_id='unit:1',image_id=None,kind='note_passage',group_id='g',source_order=0,text='创业资料秘密摘录',sender_name=None,sent_at=None)])
    provider=Provider(actions)
    service=ConversationAgentService(repository=repository,mixed_search=MixedSearchService(repository,ContentRepository(repository)),generation=SimpleNamespace(),documents=SimpleNamespace(),provider_factory=lambda _:provider,provider='fake',model='fixture',clock=kwargs.pop('clock',lambda:NOW),**kwargs)
    conversation=service.create_conversation(session_id='owner')
    conversation=service.replace_sources(conversation['conversation_id'],sources=[dict(kind='content_batch',id='notes',label='笔记')],session_id='owner')
    return service,provider,repository,conversation['conversation_id']

def approve(service,cid):
    run=service.get_conversation(cid,session_id='owner')['active_run']; approval=run['approval']
    service.approve(cid,run['run_id'],approval_id=approval['approval_id'],preview_fingerprint=approval['preview_fingerprint'],confirmed=True,session_id='owner')
    service.advance(cid,run['run_id'],session_id='owner')
    return service.get_conversation(cid,session_id='owner')

def test_model_observes_search_feedback_only_after_its_separate_approval(tmp_path):
    service,provider,repo,cid=setup(tmp_path,[SEARCH,{'tool':'ask_user','arguments':{'question':'请补充范围'}}])
    first=service.send_message(cid,text='整理创业想法',client_message_id='first',session_id='owner')
    assert not provider.requests
    payload=json.loads(first['active_run']['approval']['outbound_payloads'][0])
    assert payload['operation']=='agent_tool' and payload['tool_results']==[]
    assert '秘密摘录' not in json.dumps(payload,ensure_ascii=False)
    second=approve(service,cid)
    feedback=json.loads(second['active_run']['approval']['outbound_payloads'][0])
    assert feedback['tool_results']==[{'tool':'search_sources','terms':['创业'],'hit_count':1}]
    assert len(provider.requests)==1
    assert provider.requests[0].payload.decode()==first['active_run']['approval']['outbound_payloads'][0]
    final=approve(service,cid)
    assert final['active_run']['state']=='needs_input'
    assert final['messages'][-1]['text']=='请补充范围'
    stored=repo._connection.execute('SELECT state_json FROM chat_agent_conversations').fetchone()[0]
    assert '秘密摘录' not in stored and 'outbound_payloads' not in stored

def test_followup_idempotency_owner_and_restart(tmp_path):
    service,provider,repo,cid=setup(tmp_path,[])
    a=service.send_message(cid,text='整理创业想法',client_message_id='first',session_id='owner')
    assert service.send_message(cid,text='整理创业想法',client_message_id='first',session_id='owner')==a
    with pytest.raises(LookupError):service.get_conversation(cid,session_id='other')
    b=service.send_message(cid,text='保留商业模式',client_message_id='second',session_id='owner')
    payload=json.loads(b['active_run']['approval']['outbound_payloads'][0])
    assert payload['requests']==['整理创业想法','保留商业模式']
    assert '整理创业想法' in payload['instruction'] and '保留商业模式' in payload['instruction']
    service._pending.clear()
    restored=service.get_conversation(cid,session_id='owner')
    assert restored['active_run']['state']=='cancelled' and restored['active_run']['approval'] is None
    assert not provider.requests

@pytest.mark.parametrize('action',[
    {'tool':'read_file','arguments':{}},
    {'tool':'search_sources','arguments':{'terms':[''] ,'summary_type':'ideas','goal':'x'}},
    {'tool':'generate_document','arguments':{}},
])
def test_unavailable_or_unbounded_tools_fail_safely(tmp_path,action):
    service,provider,repo,cid=setup(tmp_path,[action])
    service.send_message(cid,text='整理创业想法',client_message_id='first',session_id='owner')
    final=approve(service,cid)
    assert final['active_run']['state']=='failed'
    assert '秘密摘录' not in json.dumps(final,ensure_ascii=False)

def test_expired_sources_cannot_be_approved_and_overlong_context_asks(tmp_path):
    now=[NOW]
    service,provider,repo,cid=setup(tmp_path,[],clock=lambda:now[0])
    service.send_message(cid,text='创业'+'长'*500,client_message_id='first',session_id='owner')
    result=service.send_message(cid,text='更多要求'*10,client_message_id='second',session_id='owner')
    assert result['active_run']['state']=='needs_input' and not provider.requests
    now[0]+=timedelta(days=7)
    with pytest.raises(PermissionError):
        service.replace_sources(cid,sources=[dict(kind='content_batch',id='notes',label='笔记')],session_id='owner')

def test_two_round_generation_automatically_delivers_distinct_immutable_artifacts(tmp_path):
    from chat_to_howto.providers.fake import FakeProvider
    from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator
    from chat_to_howto.services.generation_cache import GenerationCacheSettings
    from chat_to_howto.services.documents import DocumentService
    from chat_to_howto.services.document_text_pair import render_document_text_pair
    service,control,repo,cid=setup(tmp_path,[SEARCH,{'tool':'generate_document','arguments':{}},SEARCH,{'tool':'generate_document','arguments':{}}])
    generation_provider=FakeProvider()
    service._generation=GenerationJobOrchestrator(repository=repo,provider_factory=lambda _:generation_provider,
        cache_settings=GenerationCacheSettings(prompt_version='topic-editorial-v10-actor-evaluation',output_schema_version='1',chunk_strategy_version='1',max_chunk_input_tokens=4000,provider_behavior_revision='1'),mixed_search=service._search,clock=lambda:NOW)
    service._documents=DocumentService(repository=repo,clock=lambda:NOW)
    artifacts=[]
    for index,text in enumerate(['整理创业想法','保留商业模式']):
        service.send_message(cid,text=text,client_message_id=str(index),session_id='owner')
        approve(service,cid)
        generation=approve(service,cid)
        assert generation['active_run']['approval']['kind']=='generation'
        assert generation_provider.call_count==index
        assert '创业资料秘密摘录' in generation['active_run']['approval']['outbound_payloads'][0]
        completed=approve(service,cid)
        assert completed['active_run']['state']=='complete',completed['active_run']
        artifact=completed['messages'][-1]
        assert artifact['artifact_revision']==index+1
        doc=service._documents.get_document(artifact['document_id'],session_id='owner')
        pair=render_document_text_pair(doc,version_id=artifact['version_id'])
        assert pair.document_id==artifact['document_id'] and pair.version_id==artifact['version_id']
        assert '创业资料秘密摘录' in pair.reference.text
        artifacts.append(pair)
    assert artifacts[0].document_id!=artifacts[1].document_id
    assert len([m for m in completed['messages'] if m['document_id']])==2
    original=service._documents.get_document(artifacts[0].document_id,session_id='owner')
    assert render_document_text_pair(original,version_id=artifacts[0].version_id)==artifacts[0]
    final_payload=json.loads(control.requests[-1].payload)
    assert final_payload['requests']==['整理创业想法','保留商业模式']


def test_late_model_response_after_cancel_never_restores_run(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered,released=Event(),Event()
    service,provider,repo,cid=setup(tmp_path,[SEARCH])
    original=provider.generate
    async def delayed(request,cancellation):
        entered.set()
        await __import__('asyncio').to_thread(released.wait,3)
        return await original(request,cancellation)
    provider.generate=delayed
    first=service.send_message(cid,text='创业',client_message_id='first',session_id='owner')
    run=first['active_run']; approval=run['approval']
    service.approve(cid,run['run_id'],approval_id=approval['approval_id'],preview_fingerprint=approval['preview_fingerprint'],confirmed=True,session_id='owner')
    with ThreadPoolExecutor() as pool:
        future=pool.submit(service.advance,cid,run['run_id'],session_id='owner')
        assert entered.wait(2)
        service.cancel(cid,run['run_id'],session_id='owner')
        released.set(); future.result(timeout=5)
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='cancelled'


def test_approval_is_owner_bound_single_use_and_shared_daily_limit(tmp_path):
    service,provider,repo,cid=setup(tmp_path,[SEARCH],max_daily_provider_requests=1)
    first=service.send_message(cid,text='创业',client_message_id='first',session_id='owner')
    run=first['active_run']; approval=run['approval']
    kwargs=dict(approval_id=approval['approval_id'],preview_fingerprint=approval['preview_fingerprint'],confirmed=True)
    with pytest.raises(LookupError):service.approve(cid,run['run_id'],session_id='other',**kwargs)
    with pytest.raises(PermissionError):service.approve(cid,run['run_id'],session_id='owner',**{**kwargs,'preview_fingerprint':'0'*64})
    approve(service,cid)
    with pytest.raises(PermissionError):service.approve(cid,run['run_id'],session_id='owner',**kwargs)
    failed=approve(service,cid)
    assert failed['active_run']['error_code']=='daily_usage_limit' and len(provider.requests)==1


def test_zero_hit_search_research_and_four_control_bound(tmp_path):
    misses={**SEARCH,'arguments':{**SEARCH['arguments'],'terms':['不存在']}}
    service,provider,repo,cid=setup(tmp_path,[misses,misses,SEARCH,SEARCH])
    service.send_message(cid,text='创业',client_message_id='first',session_id='owner')
    assert json.loads(approve(service,cid)['active_run']['approval']['outbound_payloads'][0])['tool_results'][-1]['hit_count']==0
    approve(service,cid); approve(service,cid)
    failed=approve(service,cid)
    assert failed['active_run']['state']=='failed' and failed['active_run']['error_code']=='agent_tool_limit'
    assert len(provider.requests)==4

def test_global_sweep_drops_expired_preview_without_reopening_old_conversation(tmp_path):
    now=[NOW]
    service,provider,repo,cid=setup(tmp_path,[],clock=lambda:now[0])
    started=service.send_message(cid,text='创业',client_message_id='first',session_id='owner')
    run_id=started['active_run']['run_id']
    now[0]+=timedelta(minutes=6)
    service.list_conversations(session_id='different-owner')
    assert run_id not in service._pending
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='cancelled'


def test_new_sources_on_old_conversation_use_import_deadline_and_old_messages_do_not_renew(tmp_path):
    now=[NOW]
    service,provider,repo,cid=setup(tmp_path,[],clock=lambda:now[0])
    service.send_message(cid,text='创业旧要求',client_message_id='old',session_id='owner')
    now[0]+=timedelta(days=8)
    ContentRepository(repo).save_batch(batch_id='new-notes',session_digest=digest('owner'),created_at_utc=now[0].isoformat(),images=[],units=[dict(unit_id='unit:2',image_id=None,kind='note_passage',group_id='g',source_order=0,text='创业新资料',sender_name=None,sent_at=None)])
    service.replace_sources(cid,sources=[dict(kind='content_batch',id='new-notes',label='新笔记')],session_id='owner')
    new=service.send_message(cid,text='创业新要求',client_message_id='new',session_id='owner')
    assert new['sources'][0]['id']=='new-notes'
    payload=json.loads(new['active_run']['approval']['outbound_payloads'][0])
    assert payload['requests']==['创业新要求'] and '旧要求' not in json.dumps(payload,ensure_ascii=False)
    assert 'expires_at' not in new['sources'][0]

def test_global_sweep_clears_preview_after_explicit_source_retirement(tmp_path):
    service,provider,repo,cid=setup(tmp_path,[])
    started=service.send_message(cid,text='创业',client_message_id='first',session_id='owner')
    repo.retire_raw(target_kind='content_batch',target_id='notes',session_digest=digest('owner'),created_at_utc=NOW.isoformat())
    service.sweep()
    assert started['active_run']['run_id'] not in service._pending
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='cancelled'


def test_authorized_running_work_is_not_cancelled_at_preview_five_minute_expiry(tmp_path):
    now=[NOW]
    service,provider,repo,cid=setup(tmp_path,[SEARCH],clock=lambda:now[0])
    started=service.send_message(cid,text='创业',client_message_id='first',session_id='owner')
    run=started['active_run']; a=run['approval']
    service.approve(cid,run['run_id'],approval_id=a['approval_id'],preview_fingerprint=a['preview_fingerprint'],confirmed=True,session_id='owner')
    now[0]+=timedelta(minutes=6)
    service.sweep()
    assert run['run_id'] in service._pending
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='running'
    now[0]=NOW+timedelta(days=7)
    service.sweep()
    assert run['run_id'] not in service._pending
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='cancelled'

def completed_fixture(tmp_path, *, extra_actions=()):
    from chat_to_howto.providers.fake import FakeProvider
    from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator
    from chat_to_howto.services.generation_cache import GenerationCacheSettings
    from chat_to_howto.services.documents import DocumentService
    service,provider,repo,cid=setup(tmp_path,[SEARCH,{'tool':'generate_document','arguments':{}},*extra_actions])
    generation_provider=FakeProvider()
    service._generation=GenerationJobOrchestrator(repository=repo,provider_factory=lambda _:generation_provider,
        cache_settings=GenerationCacheSettings(prompt_version='topic-editorial-v10-actor-evaluation',output_schema_version='1',chunk_strategy_version='1',max_chunk_input_tokens=4000,provider_behavior_revision='1'),mixed_search=service._search,clock=lambda:NOW)
    service._documents=DocumentService(repository=repo,clock=lambda:NOW)
    service.send_message(cid,text='整理创业想法',client_message_id='first',session_id='owner')
    approve(service,cid);approve(service,cid)
    complete=approve(service,cid)
    assert complete['active_run']['state']=='complete'
    return service,repo,cid,complete['messages'][-1]


def remove_raw_source(service,repo):
    from chat_to_howto.services.deletion import DeletionService
    DeletionService(repository=repo,approved_roots={},clock=lambda:NOW,
        cancel_generation_jobs=service._generation.revoke_jobs).expire_raw(
        target_kind='content_batch',target_id='notes',session_hash=digest('owner'))


def test_sweep_recovers_after_raw_cleanup_already_deleted_running_generation_job(tmp_path):
    service,repo,cid,artifact=completed_fixture(tmp_path,extra_actions=[SEARCH,{'tool':'generate_document','arguments':{}}])
    service.send_message(cid,text='补充商业模式',client_message_id='second',session_id='owner')
    approve(service,cid);waiting=approve(service,cid)
    run=waiting['active_run'];a=run['approval']
    service.approve(cid,run['run_id'],approval_id=a['approval_id'],preview_fingerprint=a['preview_fingerprint'],confirmed=True,session_id='owner')
    pending=service._pending[run['run_id']]
    consent=service._generation.issue_consent(pending.generation_preview.preview_id,session_id='owner',confirmed=True)
    job=service._generation.start_job(consent.consent_id,session_id='owner',idempotency_key='pending-cleanup')
    pending.job_id=job.job_id
    remove_raw_source(service,repo)
    with pytest.raises(ValueError):repo.get_generation_job(job.job_id)
    service.sweep()
    assert run['run_id'] not in service._pending
    restored=service.get_conversation(cid,session_id='owner')
    assert restored['active_run']['state']=='cancelled'
    assert restored['messages'][1]['document_id']==artifact['document_id']
    doc=service._documents.get_document(artifact['document_id'],session_id='owner')
    assert doc.current_version.version_id==artifact['version_id']


def test_completed_conversation_drops_deleted_source_chips_and_accepts_new_upload(tmp_path):
    service,repo,cid,artifact=completed_fixture(tmp_path)
    remove_raw_source(service,repo)
    restored=service.get_conversation(cid,session_id='owner')
    assert restored['active_run']['state']=='complete' and restored['sources']==[]
    assert restored['messages'][-1]['document_id']==artifact['document_id']
    ContentRepository(repo).save_batch(batch_id='new',session_digest=digest('owner'),created_at_utc=NOW.isoformat(),images=[],units=[dict(unit_id='unit:new',image_id=None,kind='note_passage',group_id='g',source_order=0,text='创业新笔记',sender_name=None,sent_at=None)])
    restored=service.replace_sources(cid,sources=[*restored['sources'],dict(kind='content_batch',id='new',label='新资料')],session_id='owner')
    assert restored['sources']==[dict(kind='content_batch',id='new',label='新资料')]

def test_cached_generation_approval_has_zero_outbound_calls_and_delivers_new_document(tmp_path):
    service,repo,first_cid,first_artifact=completed_fixture(tmp_path,extra_actions=[SEARCH,{'tool':'generate_document','arguments':{}}])
    generation_provider=service._generation._provider_factory('fake')
    calls=generation_provider.call_count
    second=service.create_conversation(session_id='owner')
    cid=second['conversation_id']
    service.replace_sources(cid,sources=[dict(kind='content_batch',id='notes',label='笔记')],session_id='owner')
    service.send_message(cid,text='整理创业想法',client_message_id='cached-first',session_id='owner')
    approve(service,cid)
    waiting=approve(service,cid)
    approval=waiting['active_run']['approval']
    assert approval['kind']=='generation'
    assert approval['outbound_payloads']==[] and approval['provider_calls']==0
    completed=approve(service,cid)
    assert completed['active_run']['state']=='complete'
    assert generation_provider.call_count==calls
    second_artifact=completed['messages'][-1]
    assert second_artifact['document_id']!=first_artifact['document_id']
    for artifact in [first_artifact,second_artifact]:
        doc=service._documents.get_document(artifact['document_id'],session_id='owner')
        assert doc.current_version.version_id==artifact['version_id']

def automatic_fixture(tmp_path, actions, **options):
    from chat_to_howto.providers.fake import FakeProvider
    from chat_to_howto.services.generation_jobs import GenerationJobOrchestrator
    from chat_to_howto.services.generation_cache import GenerationCacheSettings
    from chat_to_howto.services.documents import DocumentService
    service,control,repo,cid=setup(tmp_path,actions,**options)
    generated=FakeProvider()
    service._generation=GenerationJobOrchestrator(repository=repo,provider_factory=lambda _:generated,
        cache_settings=GenerationCacheSettings(prompt_version='topic-editorial-v10-actor-evaluation',output_schema_version='1',chunk_strategy_version='1',max_chunk_input_tokens=4000,provider_behavior_revision='1'),mixed_search=service._search,clock=lambda:NOW)
    service._documents=DocumentService(repository=repo,clock=lambda:NOW)
    return service,control,generated,repo,cid


def test_new_automatic_message_runs_search_feedback_and_verified_generation_without_manual_approval(tmp_path):
    service,control,generated,repo,cid=automatic_fixture(tmp_path,[SEARCH,{'tool':'generate_document','arguments':{}}])
    started=service.send_message(cid,text='整理创业想法',client_message_id='auto',session_id='owner',automatic=True)
    assert started['active_run']['state']=='running' and started['active_run']['approval'] is None
    assert not control.requests and generated.call_count==0
    service.run_automatic(cid,started['active_run']['run_id'],session_id='owner')
    completed=service.get_conversation(cid,session_id='owner')
    assert completed['active_run']['state']=='complete',completed['active_run']
    assert len(control.requests)==2 and generated.call_count==1
    assert json.loads(control.requests[1].payload)['tool_results'][0]['hit_count']==1
    artifact=completed['messages'][-1]
    document=service._documents.get_document(artifact['document_id'],session_id='owner')
    assert document.current_version.version_id==artifact['version_id']


def test_automatic_authority_cannot_run_other_owner_or_old_manual_run(tmp_path):
    service,control,generated,repo,cid=automatic_fixture(tmp_path,[SEARCH,{'tool':'generate_document','arguments':{}}])
    manual=service.send_message(cid,text='创业',client_message_id='manual',session_id='owner')
    service.run_automatic(cid,manual['active_run']['run_id'],session_id='owner')
    assert not control.requests
    auto=service.send_message(cid,text='保留观点',client_message_id='auto',session_id='owner',automatic=True)
    service.run_automatic(cid,auto['active_run']['run_id'],session_id='other')
    assert not control.requests
    service._pending.clear()
    service.run_automatic(cid,auto['active_run']['run_id'],session_id='owner')
    assert not control.requests
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='cancelled'


@pytest.mark.parametrize('daily_limit,actions,expected',[
    (100,[{'tool':'search_sources','arguments':{**SEARCH['arguments'],'terms':['不存在']}},{'tool':'ask_user','arguments':{'question':'请补充来源'}}],'needs_input'),
    (1,[SEARCH],'failed'),
])
def test_automatic_run_still_obeys_zero_hits_and_shared_limits(tmp_path,daily_limit,actions,expected):
    service,control,generated,repo,cid=automatic_fixture(tmp_path,actions,max_daily_provider_requests=daily_limit)
    started=service.send_message(cid,text='创业',client_message_id='auto',session_id='owner',automatic=True)
    service.run_automatic(cid,started['active_run']['run_id'],session_id='owner')
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']==expected
    assert generated.call_count==0


def test_late_automatic_model_after_cancel_cannot_continue_or_generate(tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    service,control,generated,repo,cid=automatic_fixture(tmp_path,[SEARCH,{'tool':'generate_document','arguments':{}}])
    entered,released=Event(),Event()
    original=control.generate
    async def delayed(request,cancellation):
        entered.set();await asyncio.to_thread(released.wait,3)
        return await original(request,cancellation)
    control.generate=delayed
    started=service.send_message(cid,text='创业',client_message_id='auto',session_id='owner',automatic=True)
    run_id=started['active_run']['run_id']
    with ThreadPoolExecutor() as pool:
        future=pool.submit(service.run_automatic,cid,run_id,session_id='owner')
        assert entered.wait(2)
        service.cancel(cid,run_id,session_id='owner')
        released.set();future.result(timeout=5)
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='cancelled'
    assert len(control.requests)==1 and generated.call_count==0

@pytest.mark.parametrize('replacement',['message','sources'])
def test_new_message_or_source_change_revokes_automatic_run_grant(tmp_path,replacement):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    service,control,generated,repo,cid=automatic_fixture(tmp_path,[SEARCH])
    entered,released=Event(),Event()
    original=control.generate
    async def delayed(request,cancellation):
        entered.set();await asyncio.to_thread(released.wait,3)
        return await original(request,cancellation)
    control.generate=delayed
    started=service.send_message(cid,text='创业',client_message_id='auto',session_id='owner',automatic=True)
    old_run=started['active_run']['run_id']
    with ThreadPoolExecutor() as pool:
        future=pool.submit(service.run_automatic,cid,old_run,session_id='owner')
        assert entered.wait(2)
        if replacement=='message':
            new=service.send_message(cid,text='保留商业模式',client_message_id='new-manual',session_id='owner')
            assert new['active_run']['state']=='waiting_approval'
        else:
            service.replace_sources(cid,sources=[],session_id='owner')
        released.set();future.result(timeout=5)
    current=service.get_conversation(cid,session_id='owner')
    assert old_run not in service._pending and generated.call_count==0
    assert len(control.requests)==1
    if replacement=='message':
        assert current['active_run']['run_id']!=old_run
        service.run_automatic(cid,current['active_run']['run_id'],session_id='owner')
        assert len(control.requests)==1
    else:
        assert current['active_run']['state']=='cancelled'

def test_automatic_intermediate_preview_is_publicly_running_until_background_continues(tmp_path):
    service,control,generated,repo,cid=automatic_fixture(tmp_path,[SEARCH,{'tool':'generate_document','arguments':{}}])
    started=service.send_message(cid,text='创业',client_message_id='auto',session_id='owner',automatic=True)
    run_id=started['active_run']['run_id']
    service.advance(cid,run_id,session_id='owner')
    intermediate=service.get_conversation(cid,session_id='owner')
    assert intermediate['active_run']['state']=='running'
    assert intermediate['active_run']['approval'] is None
    assert service._store.get(cid,session_digest=digest('owner'))['state']['active_run']['state']=='waiting_approval'
    service.run_automatic(cid,run_id,session_id='owner')
    assert service.get_conversation(cid,session_id='owner')['active_run']['state']=='complete'


def test_automatic_authorization_failure_stops_instead_of_remaining_publicly_running(tmp_path,monkeypatch):
    service,control,generated,repo,cid=automatic_fixture(tmp_path,[SEARCH])
    started=service.send_message(cid,text='创业',client_message_id='auto',session_id='owner',automatic=True)
    run_id=started['active_run']['run_id']
    service.advance(cid,run_id,session_id='owner')
    def rejected(*args,**kwargs):raise PermissionError('agent_authorization_unavailable')
    monkeypatch.setattr(service,'approve',rejected)
    service.run_automatic(cid,run_id,session_id='owner')
    final=service.get_conversation(cid,session_id='owner')
    assert final['active_run']['state'] in {'cancelled','failed'}
    assert final['active_run']['approval'] is None and run_id not in service._pending
    assert len(control.requests)==1 and generated.call_count==0
